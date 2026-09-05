"""Hours writers — supported channels, keeta worker-only, dry-run default."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.aggregators import hours_writers as hw


def test_supported_channels_is_httpx_only():
    assert hw.supported_channels() == frozenset(
        {"talabat", "deliveroo", "noon", "careem"}
    )
    assert "keeta" not in hw.supported_channels()


@pytest.mark.asyncio
async def test_keeta_raises_worker_reason(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    with pytest.raises(hw.HoursWriteUnsupported, match="headed worker"):
        await hw.push_hours(
            None, channel="keeta", branch=object(), opens="08:00", closes="17:00"
        )


@pytest.mark.asyncio
async def test_flag_off_raises(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    with pytest.raises(hw.HoursWriteUnsupported, match="CATALOG_SYNC_ENABLED"):
        await hw.push_hours(
            None,
            channel="deliveroo",
            branch=object(),
            opens="08:00",
            closes="17:00",
        )


@pytest.mark.asyncio
async def test_dry_run_does_not_execute(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    async def fake_mapper(_db, _branch, **_kw):
        return {
            "endpoint": "PUT /api/restaurants/1/opening_hours",
            "payload": {"hours": []},
            "session": object(),
        }

    executed: list[tuple] = []

    async def fake_execute(channel, plan):
        executed.append((channel, plan.get("op")))

    monkeypatch.setattr(hw, "_PUSHERS", {**hw._PUSHERS, "deliveroo": fake_mapper})
    monkeypatch.setattr(hw, "_execute", fake_execute)

    branch = SimpleNamespace(id="b1")
    plan = await hw.push_hours(
        object(),
        channel="deliveroo",
        branch=branch,
        opens="08:00",
        closes="17:00",
    )
    assert plan["dry_run"] is True
    assert plan["op"] == "push_hours"
    assert executed == []

    live = await hw.push_hours(
        object(),
        channel="deliveroo",
        branch=branch,
        opens="08:00",
        closes="17:00",
        dry_run=False,
    )
    assert live["dry_run"] is False
    assert executed == [("deliveroo", "push_hours")]


@pytest.mark.asyncio
async def test_close_outlet_dry_run_does_not_execute(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)

    async def fake_closer(_db, _branch, **_kw):
        return {
            "endpoint": "PUT .../vendors/1/status",
            "status": "CLOSED_TODAY",
            "payload": {"status": "CLOSED_TODAY"},
            "session": object(),
        }

    executed: list[str] = []

    async def fake_execute(channel, _plan):
        executed.append(channel)

    monkeypatch.setattr(hw, "_CLOSERS", {**hw._CLOSERS, "talabat": fake_closer})
    monkeypatch.setattr(hw, "_execute", fake_execute)

    plan = await hw.close_outlet(
        object(), channel="talabat", branch=SimpleNamespace(id="b1")
    )
    assert plan["dry_run"] is True
    assert plan["op"] == "close_outlet"
    assert executed == []
