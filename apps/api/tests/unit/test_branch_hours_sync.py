"""
The branch-hours sync — resolving each branch's window from its weekly schedule
and mirroring it to the integrators. There is no `opening_from`/`opening_to`
cache any more; `sync_branch` reports the day's window and fans out.

The schedule read is stubbed (the suite mocks the DB), so these pin the logic
that matters: an open day reports that day's window, a closed day reports the
*next* open day's window (what Foodics is set to), and a branch with no schedule
is a no-op.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import branch_hours_service, branch_hours_sync
from app.services.aggregators import hours_writers
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    AggregatorUnavailableError,
)

# 2026-09-06 is a Sunday (weekday 0 in branch_weekly_hours numbering).
SUN = date(2026, 9, 6)


def _branch() -> SimpleNamespace:
    return SimpleNamespace(id="b1", name="Karama", aggregators=[])


def _stub_schedule(monkeypatch, sched):
    async def fake(db, branch_id):
        return sched

    monkeypatch.setattr(branch_hours_service, "schedule", fake)


@pytest.mark.asyncio
async def test_open_day_reports_today_window(monkeypatch):
    _stub_schedule(monkeypatch, {0: ("09:00", "23:00"), 1: ("09:00", "23:00")})
    db = AsyncMock()
    res = await branch_hours_sync.sync_branch(db, _branch(), today=SUN)
    assert res["status"] == "open"
    assert res["window"] == "09:00-23:00"


@pytest.mark.asyncio
async def test_closed_day_reports_next_open_window(monkeypatch):
    # Open Monday only; Sunday is closed, so it reports Monday's window (what
    # Foodics's single daily window is set to).
    _stub_schedule(monkeypatch, {1: ("08:00", "22:00")})
    db = AsyncMock()
    res = await branch_hours_sync.sync_branch(db, _branch(), today=SUN)
    assert res["status"] == "closed-today"
    assert res["window"] == "08:00-22:00"


@pytest.mark.asyncio
async def test_no_schedule_is_a_noop(monkeypatch):
    _stub_schedule(monkeypatch, None)
    db = AsyncMock()
    res = await branch_hours_sync.sync_branch(db, _branch(), today=SUN)
    assert res["status"] == "no-schedule"


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
async def test_transient_hour_write_retries_and_records_attempt_count(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg.settings, "BRANCH_HOURS_SYNC_LIVE", True)
    calls = 0

    async def flaky_push(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AggregatorUnavailableError("careem returned 503")
        return {"op": "push_weekly_hours", "endpoint": "x", "weekly": {}}

    monkeypatch.setattr(hours_writers, "push_weekly_hours", flaky_push)
    monkeypatch.setattr(branch_hours_sync.asyncio, "sleep", AsyncMock())
    db = _sync_db()
    await branch_hours_sync._push_weekly_to_channel(db, _branch(), "careem", _SCHED)

    assert calls == 2
    row = _rows(db)[0]
    assert row.status == "completed"
    assert row.planned["attempts"] == 2


@pytest.mark.asyncio
async def test_auth_failure_marks_session_for_headed_recovery(monkeypatch):
    async def dead_session(*_args, **_kwargs):
        raise AggregatorAuthError("careem returned 401")

    marked = AsyncMock()
    monkeypatch.setattr(hours_writers, "push_weekly_hours", dead_session)
    monkeypatch.setattr(branch_hours_sync.session_store, "mark_needs_bootstrap", marked)
    monkeypatch.setattr(
        branch_hours_sync.alerting, "capture_issue", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        branch_hours_sync.alerting, "capture_exc", lambda *args, **kwargs: None
    )

    db = _sync_db()
    await branch_hours_sync._push_weekly_to_channel(db, _branch(), "careem", _SCHED)

    marked.assert_awaited_once_with(db, "careem", error="careem returned 401")
    assert _rows(db)[0].status == "failed"


@pytest.mark.asyncio
async def test_repeat_auth_failure_on_dead_session_does_not_realert(monkeypatch):
    # Careem's console session dies for hours; the loop keeps trying every hour.
    # A 401 on a session we already flagged needs_bootstrap must still record a
    # failed row and re-mark it, but must NOT re-fire Sentry every tick — that is
    # the intermittent "hours sync broken" noise the suppression fixes.
    async def dead_session(*_args, **_kwargs):
        raise AggregatorAuthError("careem returned 401")

    monkeypatch.setattr(hours_writers, "push_weekly_hours", dead_session)
    monkeypatch.setattr(
        branch_hours_sync.session_store,
        "status_for",
        AsyncMock(return_value="needs_bootstrap"),
    )
    marked = AsyncMock()
    monkeypatch.setattr(branch_hours_sync.session_store, "mark_needs_bootstrap", marked)
    capture_issue = MagicMock()
    capture_exc = MagicMock()
    monkeypatch.setattr(branch_hours_sync.alerting, "capture_issue", capture_issue)
    monkeypatch.setattr(branch_hours_sync.alerting, "capture_exc", capture_exc)

    db = _sync_db()
    await branch_hours_sync._push_weekly_to_channel(db, _branch(), "careem", _SCHED)

    # Still recorded and re-marked for the worker + admin panel …
    marked.assert_awaited_once_with(db, "careem", error="careem returned 401")
    assert _rows(db)[0].status == "failed"
    # … but no repeat alert, because the session was already known dead.
    capture_issue.assert_not_called()
    capture_exc.assert_not_called()


@pytest.mark.asyncio
async def test_first_auth_failure_on_live_session_alerts(monkeypatch):
    # The transition from a usable session to a dead one is news — alert once.
    async def dead_session(*_args, **_kwargs):
        raise AggregatorAuthError("careem returned 401")

    monkeypatch.setattr(hours_writers, "push_weekly_hours", dead_session)
    monkeypatch.setattr(
        branch_hours_sync.session_store,
        "status_for",
        AsyncMock(return_value=branch_hours_sync.session_store.SESSION_LIVE),
    )
    monkeypatch.setattr(
        branch_hours_sync.session_store, "mark_needs_bootstrap", AsyncMock()
    )
    capture_issue = MagicMock()
    capture_exc = MagicMock()
    monkeypatch.setattr(branch_hours_sync.alerting, "capture_issue", capture_issue)
    monkeypatch.setattr(branch_hours_sync.alerting, "capture_exc", capture_exc)

    db = _sync_db()
    await branch_hours_sync._push_weekly_to_channel(db, _branch(), "careem", _SCHED)

    capture_issue.assert_called_once()
    capture_exc.assert_called_once()


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


# ── the loop tick: leader-first, per-branch isolated sessions (F-AGG-17) ──────


@asynccontextmanager
async def _leader(*_a, **_k):
    yield True


@asynccontextmanager
async def _not_leader(*_a, **_k):
    yield False


class _BranchIdSession:
    """A SchedulerSessionFactory stand-in whose one query returns branch ids."""

    def __init__(self, branch_ids):
        self._ids = branch_ids

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, _stmt):
        ids = self._ids

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self__):
                        return ids

                return _S()

        return _R()


def _wire_tick(monkeypatch, *, leader, branch_ids, gate=True):
    monkeypatch.setattr(
        branch_hours_sync.advisory_lock, "held", _leader if leader else _not_leader
    )
    monkeypatch.setattr(branch_hours_sync.settings, "CATALOG_SYNC_ENABLED", gate)
    monkeypatch.setattr(
        branch_hours_sync,
        "SchedulerSessionFactory",
        lambda: _BranchIdSession(branch_ids),
    )
    isolated = AsyncMock(return_value="synced")
    monkeypatch.setattr(branch_hours_sync, "_sync_branch_isolated", isolated)
    return isolated


@pytest.mark.asyncio
async def test_tick_mirrors_each_active_branch_when_it_is_the_leader(monkeypatch):
    isolated = _wire_tick(monkeypatch, leader=True, branch_ids=["b1", "b2", "b3"])
    await branch_hours_sync._tick()
    assert [c.args[0] for c in isolated.await_args_list] == ["b1", "b2", "b3"]


@pytest.mark.asyncio
async def test_tick_does_nothing_when_another_slot_holds_the_lock(monkeypatch):
    isolated = _wire_tick(monkeypatch, leader=False, branch_ids=["b1"])
    await branch_hours_sync._tick()
    isolated.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_the_whole_fan_out_when_the_write_gate_is_off(monkeypatch):
    isolated = _wire_tick(monkeypatch, leader=True, branch_ids=["b1"], gate=False)
    await branch_hours_sync._tick()
    isolated.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_survives_one_branch_raising_and_still_does_the_rest(monkeypatch):
    _wire_tick(monkeypatch, leader=True, branch_ids=["b1", "b2"])
    seen: list[str] = []

    async def flaky(branch_id):
        seen.append(branch_id)
        if branch_id == "b1":
            raise RuntimeError("portal down")
        return "synced"

    monkeypatch.setattr(branch_hours_sync, "_sync_branch_isolated", flaky)
    await branch_hours_sync._tick()  # must not raise
    assert seen == ["b1", "b2"]
