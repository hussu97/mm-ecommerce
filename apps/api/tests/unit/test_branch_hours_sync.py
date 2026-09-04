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
from unittest.mock import AsyncMock

import pytest

from app.services import branch_hours_service, branch_hours_sync

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
