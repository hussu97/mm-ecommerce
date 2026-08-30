"""VM heal-gate: entrypoint skips Xvfb for heal-sessions; needs-heal is status-only."""

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


def test_entrypoint_xvfb_wraps_headed_commands_only():
    text = _noncomment(ENTRYPOINT.read_text())
    assert "exec xvfb-run" in text
    assert 'exec aggregator-bootstrap "$@"' in text
    headed = re.search(r"login\|[^\n]+", text)
    assert headed is not None, "headed-command case pattern missing"
    pattern = headed.group(0)
    assert "warm-sessions" in pattern
    assert "login" in pattern
    assert "bootstrap" in pattern
    assert "heal-sessions" not in pattern
    # Direct exec (no xvfb) is the default arm — heal-sessions lands there.
    assert text.count("exec xvfb-run") == 1
    assert text.count('exec aggregator-bootstrap "$@"') == 1


def test_entrypoint_heal_sessions_does_not_wrap_xvfb():
    """heal-sessions is not a headed case arm, so it execs the CLI with no Xvfb."""
    raw = ENTRYPOINT.read_text()
    text = _noncomment(raw)
    assert "heal-sessions" not in text
    # Always-xvfb form is gone: xvfb-run is inside the headed case only.
    lines = [ln.strip() for ln in text.splitlines()]
    xvfb_idx = next(i for i, ln in enumerate(lines) if "xvfb-run" in ln)
    # The xvfb exec sits under the headed `case` arm, after login|warm-sessions.
    before = "\n".join(lines[: xvfb_idx + 1])
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
    expired = [
        {
            "channel": "noon",
            "status": "live",
            "token_expired": True,
            "cookie_expired": False,
        }
    ]
    assert _run(live) == 1
    assert _run(dead) == 0
    assert _run(expired) == 0
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
