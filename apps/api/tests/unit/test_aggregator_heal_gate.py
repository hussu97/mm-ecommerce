"""VM heal-gate: entrypoint wraps heal-sessions in Xvfb; needs-heal is status-only.

heal-sessions MUST have a display: its auto-relogin spawns headed Chrome, which
without an X server never opens its debug port, so the every-2-min heal could
detect a dead anti-bot channel but never re-login it. (An earlier revision
deliberately skipped Xvfb for heal to save the per-tick X cost — but that made
autonomous re-login impossible, so heal is now wrapped like the other headed
commands. The curl needs-heal gate still keeps healthy ticks from starting one.)
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1 import aggregators as aggregators_api
from app.services.aggregators import crypto, session_store

ROOT = Path(__file__).resolve().parents[4]
ENTRYPOINT = ROOT / "apps" / "aggregator-bootstrap" / "docker-entrypoint.sh"
CRON = ROOT / "apps" / "aggregator-bootstrap" / "deploy" / "aggregator-warm.cron"


def _noncomment(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    )


def test_entrypoint_xvfb_wraps_headed_commands():
    text = _noncomment(ENTRYPOINT.read_text())
    assert "exec xvfb-run" in text
    assert 'exec aggregator-bootstrap "$@"' in text
    headed = re.search(r"login\|[^\n]+", text)
    assert headed is not None, "headed-command case pattern missing"
    pattern = headed.group(0)
    assert "warm-sessions" in pattern
    assert "login" in pattern
    assert "bootstrap" in pattern
    # heal-sessions is now a headed arm: its auto-relogin needs a display.
    assert "heal-sessions" in pattern
    # Exactly one xvfb arm and one direct-exec default arm.
    assert text.count("exec xvfb-run") == 1
    assert text.count('exec aggregator-bootstrap "$@"') == 1


def test_entrypoint_heal_sessions_wraps_xvfb():
    """heal-sessions sits in the Xvfb-wrapped case arm, not the direct-exec default,
    so its headed auto-relogin has a display to open Chrome against."""
    text = _noncomment(ENTRYPOINT.read_text())
    lines = [ln.strip() for ln in text.splitlines()]
    xvfb_idx = next(i for i, ln in enumerate(lines) if "xvfb-run" in ln)
    # The case pattern (with heal-sessions) precedes the xvfb exec it selects.
    before = "\n".join(lines[: xvfb_idx + 1])
    assert "heal-sessions" in before
    assert "warm-sessions" in before
    assert "login" in before


def _heal_python_source() -> str:
    heal = next(
        ln
        for ln in CRON.read_text().splitlines()
        if ln.startswith("*/2") and not ln.startswith("#")
    )
    match = re.search(r'python3 -c "((?:\\.|[^"\\])*)"', heal)
    assert match is not None, f"python3 -c snippet missing from heal line: {heal}"
    return match.group(1).replace('\\"', '"')


def test_cron_heal_python_parses_and_flags_not_live():
    code = _heal_python_source()
    ast.parse(code)

    def _run(payload) -> int:
        result = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode

    live = [
        {
            "channel": "noon",
            "status": "live",
            "token_expired": False,
            "cookie_expired": False,
        }
    ]
    dead = [
        {
            "channel": "talabat",
            "status": "needs_bootstrap",
            "token_expired": False,
            "cookie_expired": False,
        }
    ]
    # A live session whose token/cookie is (nominally) expired must NOT trigger a
    # headed heal: the flags fire heal for channels heal cannot help (Talabat's
    # rotating PerimeterX cookie, Noon's warm-only Akamai cookie, Deliveroo/Careem
    # which self-heal in the sweep), which ran a headed heal every 2 minutes all
    # day. A genuinely dead session reaches `status != live` and still trips heal.
    expired = [
        {
            "channel": "noon",
            "status": "live",
            "token_expired": True,
            "cookie_expired": True,
        }
    ]
    assert _run(live) == 1
    assert _run(dead) == 0
    assert _run(expired) == 1  # live status → no heal, despite expiry flags
    assert _run({"channels": dead}) == 0
    assert _run({"channels": live}) == 1


def test_cron_antibot_warm_omits_careem():
    antibot = next(
        ln
        for ln in CRON.read_text().splitlines()
        if ln.startswith("15 22") and not ln.startswith("#")
    )
    assert "careem" not in antibot
    assert "noon" in antibot
    assert "talabat" in antibot


async def test_needs_heal_rejected_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "")
    resp = await client.get(
        "/api/v1/aggregators/worker/needs-heal",
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


async def test_needs_heal_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.get(
        "/api/v1/aggregators/worker/needs-heal",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_needs_heal_rejects_missing_token(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.get("/api/v1/aggregators/worker/needs-heal")
    assert resp.status_code == 401


async def test_needs_heal_returns_status_only(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")

    async def fake_list(_db):
        return [
            {
                "channel": "noon",
                "status": "live",
                "token_expired": False,
                "cookie_expired": False,
            },
            {
                "channel": "talabat",
                "status": "needs_bootstrap",
                "token_expired": False,
                "cookie_expired": False,
            },
        ]

    monkeypatch.setattr(aggregators_api.session_store, "list_heal_channels", fake_list)
    resp = await client.get(
        "/api/v1/aggregators/worker/needs-heal",
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body[0] == {
        "channel": "noon",
        "status": "live",
        "token_expired": False,
        "cookie_expired": False,
    }
    assert body[1]["channel"] == "talabat"
    assert body[1]["status"] == "needs_bootstrap"
    for row in body:
        assert "cookies" not in row
        assert "tokens" not in row
        assert "storage_state" not in row


def test_needs_heal_source_does_not_decrypt():
    route_src = inspect.getsource(aggregators_api.worker_needs_heal)
    assert "list_worker_bundles" not in route_src
    assert "decrypt_json" not in route_src
    assert "list_heal_channels" in route_src
    store_src = inspect.getsource(session_store.list_heal_channels)
    assert "decrypt_json" not in store_src
    assert "list_worker_bundles" not in store_src
    assert "cookies_encrypted" not in store_src
    assert "storage_state_encrypted" not in store_src


async def test_list_heal_channels_does_not_call_decrypt(monkeypatch):
    monkeypatch.setattr(
        crypto,
        "decrypt_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("decrypt")),
    )

    async def boom_bundles(*_a, **_k):
        raise AssertionError("list_worker_bundles")

    monkeypatch.setattr(session_store, "list_worker_bundles", boom_bundles)

    now = datetime.now(timezone.utc)
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [
                ("noon", "live", None, None),
                ("talabat", "needs_bootstrap", now - timedelta(hours=1), None),
            ]
        )
    )
    out = await session_store.list_heal_channels(db)
    assert [r["channel"] for r in out] == ["noon", "talabat"]
    assert out[0]["status"] == "live"
    assert out[0]["token_expired"] is False
    assert out[1]["status"] == "needs_bootstrap"
    assert out[1]["token_expired"] is True
    assert out[1]["cookie_expired"] is False


async def test_list_heal_channels_talabat_cookie_expiry_is_advisory():
    """Talabat's rotating PerimeterX cookie must report cookie_expired=False even
    when its nominal TTL has passed — otherwise the 2-minute heal cron re-warmed it
    headed all day. Noon's expired cookie still reports True."""
    now = datetime.now(timezone.utc)
    past = now - timedelta(minutes=10)
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            all=lambda: [
                ("noon", "live", None, past),
                ("talabat", "live", None, past),
            ]
        )
    )
    out = await session_store.list_heal_channels(db)
    by_ch = {r["channel"]: r for r in out}
    assert by_ch["noon"]["cookie_expired"] is True
    assert by_ch["talabat"]["cookie_expired"] is False
