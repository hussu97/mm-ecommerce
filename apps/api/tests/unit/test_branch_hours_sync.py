"""
The daily branch-hours sync — deriving `opening_from`/`opening_to` from the
weekly schedule.

The schedule read is stubbed (the suite mocks the DB), so these pin the logic
that matters: an open day stamps that day's window, a closed day (holiday or a
closed weekday) shows the *next* open day's window rather than a stale one, a
branch with no schedule is left alone, and a branch already showing the right
window is not rewritten.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import branch_hours_service, branch_hours_sync
from app.services.aggregators import hours_writers

# 2026-09-06 is a Sunday (weekday 0 in branch_weekly_hours numbering).
SUN = date(2026, 9, 6)


def _branch() -> SimpleNamespace:
    return SimpleNamespace(
        id="b1", name="Karama", opening_from="00:00", opening_to="23:59"
    )


def _stub_schedule(monkeypatch, sched):
    async def fake(db, branch_id):
        return sched

    monkeypatch.setattr(branch_hours_service, "schedule", fake)


@pytest.mark.asyncio
async def test_open_day_stamps_today_window(monkeypatch):
    _stub_schedule(monkeypatch, {0: ("09:00", "23:00"), 1: ("09:00", "23:00")})
    db = AsyncMock()
    branch = _branch()
    res = await branch_hours_sync.sync_branch(db, branch, today=SUN)
    assert (branch.opening_from, branch.opening_to) == ("09:00", "23:00")
    assert res["status"] == "open"
    assert res["updated"] is True
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_closed_day_shows_next_open_window(monkeypatch):
    # Open Monday only; Sunday is closed, so it should show Monday's window.
    _stub_schedule(monkeypatch, {1: ("08:00", "22:00")})
    db = AsyncMock()
    branch = _branch()
    res = await branch_hours_sync.sync_branch(db, branch, today=SUN)
    assert res["status"] == "closed-today"
    assert res["window"] == "08:00-22:00"
    assert (branch.opening_from, branch.opening_to) == ("08:00", "22:00")


@pytest.mark.asyncio
async def test_no_schedule_leaves_window_untouched(monkeypatch):
    _stub_schedule(monkeypatch, None)
    db = AsyncMock()
    branch = _branch()
    res = await branch_hours_sync.sync_branch(db, branch, today=SUN)
    assert res["status"] == "no-schedule"
    assert (branch.opening_from, branch.opening_to) == ("00:00", "23:59")
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotent_when_window_already_correct(monkeypatch):
    _stub_schedule(monkeypatch, {0: ("09:00", "23:00")})
    db = AsyncMock()
    branch = _branch()
    branch.opening_from, branch.opening_to = "09:00", "23:00"
    res = await branch_hours_sync.sync_branch(db, branch, today=SUN)
    assert res["updated"] is False
    db.flush.assert_not_awaited()


# ── fan-out to the integrators ────────────────────────────────────────────────

_SCHED = {0: ("09:00", "23:00"), 1: ("08:00", "22:00")}


def _rows(db):
    """The BranchHoursSyncRun rows the code added this call."""
    return [c.args[0] for c in db.add.call_args_list]


def _sync_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # code adds run rows without awaiting
    return db


@pytest.mark.asyncio
async def test_gate_off_pushes_nothing(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", False)
    pushed = AsyncMock()
    monkeypatch.setattr(hours_writers, "push_weekly_hours", pushed)
    db = _sync_db()
    branch = SimpleNamespace(id="b1", name="X", aggregators=["noon"])
    await branch_hours_sync._push_to_channels(
        db, branch, _SCHED, display=("09:00", "23:00")
    )
    pushed.assert_not_awaited()
    assert _rows(db) == []


@pytest.mark.asyncio
async def test_weekly_push_per_aggregator_skips_keeta(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg.settings, "BRANCH_HOURS_SYNC_LIVE", False)

    calls: list[dict] = []

    async def fake_push(db, *, channel, branch, weekly, dry_run):
        calls.append({"channel": channel, "weekly": weekly, "dry_run": dry_run})
        return {"op": "push_weekly_hours", "endpoint": f"x/{channel}", "weekly": {}}

    monkeypatch.setattr(hours_writers, "push_weekly_hours", fake_push)
    # No Foodics map for this branch.
    monkeypatch.setattr(branch_hours_sync, "_push_foodics", AsyncMock())

    db = _sync_db()
    branch = SimpleNamespace(id="b1", name="X", aggregators=["noon", "careem", "keeta"])
    await branch_hours_sync._push_to_channels(
        db, branch, _SCHED, display=("09:00", "23:00")
    )

    # keeta is skipped (worker's job); the other two get the WHOLE schedule.
    assert [c["channel"] for c in calls] == ["noon", "careem"]
    assert all(c["weekly"] == _SCHED and c["dry_run"] is True for c in calls)
    rows = _rows(db)
    assert {r.channel for r in rows} == {"noon", "careem"}
    assert all(r.status == "completed" and r.dry_run is True for r in rows)


@pytest.mark.asyncio
async def test_live_flag_sets_dry_run_false(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg.settings, "BRANCH_HOURS_SYNC_LIVE", True)

    seen: list[bool] = []

    async def fake_push(db, *, channel, branch, weekly, dry_run):
        seen.append(dry_run)
        return {"op": "push_weekly_hours", "endpoint": "x", "weekly": {}}

    monkeypatch.setattr(hours_writers, "push_weekly_hours", fake_push)
    monkeypatch.setattr(branch_hours_sync, "_push_foodics", AsyncMock())
    db = _sync_db()
    branch = SimpleNamespace(id="b1", name="X", aggregators=["noon"])
    await branch_hours_sync._push_to_channels(
        db, branch, _SCHED, display=("09:00", "23:00")
    )
    assert seen == [False]


@pytest.mark.asyncio
async def test_channel_failure_records_and_alerts_then_continues(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg.settings, "BRANCH_HOURS_SYNC_LIVE", False)

    async def fake_push(db, *, channel, branch, weekly, dry_run):
        if channel == "noon":
            raise RuntimeError("dead session")
        return {"op": "push_weekly_hours", "endpoint": "x", "weekly": {}}

    issues: list[tuple] = []
    monkeypatch.setattr(hours_writers, "push_weekly_hours", fake_push)
    monkeypatch.setattr(branch_hours_sync, "_push_foodics", AsyncMock())
    monkeypatch.setattr(
        branch_hours_sync.alerting,
        "capture_issue",
        lambda msg, **kw: issues.append((msg, kw["fingerprint"])),
    )
    monkeypatch.setattr(branch_hours_sync.alerting, "capture_exc", lambda *a, **k: None)

    db = _sync_db()
    branch = SimpleNamespace(id="b1", name="X", aggregators=["noon", "careem"])
    await branch_hours_sync._push_to_channels(
        db, branch, _SCHED, display=("09:00", "23:00")
    )

    rows = {r.channel: r for r in _rows(db)}
    assert rows["noon"].status == "failed" and "dead session" in rows["noon"].error
    assert rows["careem"].status == "completed"  # the failure did not abort the loop
    assert issues and issues[0][1] == ["branch-hours-sync", "noon", "weekly-push"]


@pytest.mark.asyncio
async def test_foodics_daily_push_dry_run(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg.settings, "BRANCH_HOURS_SYNC_LIVE", False)

    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(foodics_branch_id="fb1")
    db = _sync_db()
    db.execute = AsyncMock(return_value=result)

    branch = SimpleNamespace(id="b1", name="X")
    await branch_hours_sync._push_foodics(db, branch, ("09:00", "23:00"))

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0].channel == "foodics"
    assert rows[0].status == "completed" and rows[0].dry_run is True
    assert rows[0].planned["window"] == "09:00-23:00"


@pytest.mark.asyncio
async def test_foodics_no_map_records_nothing(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = _sync_db()
    db.execute = AsyncMock(return_value=result)
    branch = SimpleNamespace(id="b1", name="X")
    await branch_hours_sync._push_foodics(db, branch, ("09:00", "23:00"))
    assert _rows(db) == []
