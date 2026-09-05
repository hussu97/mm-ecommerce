"""Keeta (and Deliveroo menu) pulls must reopen the persistent Chrome profile.

Hydrating cookies into a fresh `storage_state` context is the Keeta outage:
LOGIN_ACCOUNTID empties within minutes because the merchant session is bound
to `keeta.chrome`. No real browser is launched — `_open_context` is mocked.
"""

from __future__ import annotations

import pytest

from aggregator_bootstrap import warm
from aggregator_bootstrap.browser import NotLoggedInError
from aggregator_bootstrap.config import settings


class _PlaywrightCM:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class _Opened:
    context = object()

    async def close(self):
        return None


def _stub_persistent(monkeypatch, tmp_path, channel: str) -> list:
    """Point STORAGE_STATE_DIR at tmp, create `{channel}.chrome`, capture opens."""
    monkeypatch.setattr(settings, "STORAGE_STATE_DIR", str(tmp_path))
    (tmp_path / f"{channel}.chrome").mkdir()
    opened: list = []

    async def fake_open(pw, ch, *, headed):
        opened.append({"channel": ch, "headed": headed, "persistent": True})
        return _Opened()

    async def fake_storage(*a, **k):
        raise AssertionError("must not hydrate into a fresh storage_state context")

    monkeypatch.setattr("aggregator_bootstrap.engine.async_playwright", _PlaywrightCM)
    monkeypatch.setattr("aggregator_bootstrap.browser._open_context", fake_open)
    monkeypatch.setattr(
        "aggregator_bootstrap.browser._open_storage_state_context", fake_storage
    )
    return opened


def test_persistent_profile_or_raise_requires_chrome_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_STATE_DIR", str(tmp_path))
    (tmp_path / "keeta.session.json").write_text("{}")  # storage_state is NOT enough
    with pytest.raises(NotLoggedInError, match="keeta chrome profile missing"):
        warm._persistent_profile_or_raise("keeta")


def test_persistent_profile_or_raise_accepts_chrome_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_STATE_DIR", str(tmp_path))
    profile = tmp_path / "keeta.chrome"
    profile.mkdir()
    assert warm._persistent_profile_or_raise("keeta") == profile


async def test_pull_keeta_orders_opens_persistent_profile(monkeypatch, tmp_path):
    opened = _stub_persistent(monkeypatch, tmp_path, "keeta")

    async def fake_fetch(context, *, months_back):
        assert months_back == 0
        assert context is _Opened.context
        return []

    monkeypatch.setattr(warm, "fetch_keeta_orders", fake_fetch)

    result = await warm.pull_keeta_orders_in_page()
    assert opened == [
        {"channel": "keeta", "headed": not settings.HEADLESS, "persistent": True}
    ]
    assert result["payloads"] == 0


async def test_pull_keeta_menu_opens_persistent_profile(monkeypatch, tmp_path):
    opened = _stub_persistent(monkeypatch, tmp_path, "keeta")

    async def fake_menu(context):
        return []

    monkeypatch.setattr("aggregator_bootstrap.keeta_pull.fetch_keeta_menu", fake_menu)

    result = await warm.pull_keeta_menu_in_page()
    assert opened[0]["channel"] == "keeta"
    assert opened[0]["persistent"] is True
    assert result["payloads"] == 0


async def test_write_keeta_hours_opens_persistent_profile(monkeypatch, tmp_path):
    opened = _stub_persistent(monkeypatch, tmp_path, "keeta")

    async def fake_write(
        context, *, windows=None, wait_seconds=8, persist=False, dry_run=False
    ):
        return {
            "saved": 0,
            "probed": True,
            "captured_xhrs": [],
            "all_shop_posts": [],
            "save_path": None,
            "wait_seconds": wait_seconds,
            "persist": persist,
            "dry_run": dry_run,
        }

    monkeypatch.setattr(
        "aggregator_bootstrap.keeta_pull.write_keeta_today_hours", fake_write
    )

    result = await warm.write_keeta_hours_in_page()
    assert opened == [
        {"channel": "keeta", "headed": not settings.HEADLESS, "persistent": True}
    ]
    assert result["probed"] is True


async def test_pull_deliveroo_menu_opens_persistent_profile(monkeypatch, tmp_path):
    opened = _stub_persistent(monkeypatch, tmp_path, "deliveroo")

    async def fake_account(*_a, **_k):
        return None

    async def fake_fetch(context):
        return []

    monkeypatch.setattr(warm, "pull_account", fake_account)
    monkeypatch.setattr(
        "aggregator_bootstrap.deliveroo_pull.fetch_deliveroo_menu", fake_fetch
    )

    result = await warm.pull_deliveroo_menu_in_page()
    assert opened[0]["channel"] == "deliveroo"
    assert opened[0]["persistent"] is True
    assert result["payloads"] == 0
