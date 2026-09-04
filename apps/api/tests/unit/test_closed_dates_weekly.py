"""
Weekly-closed weekdays fold into `closed_dates_for`.

The whole design rests on this: a closed weekday must read exactly like a
holiday, so that every open/closed consumer already threading `closed_dates`
through `trading_hours` becomes weekly-schedule-aware with no change of its own.
Here the holiday query returns nothing and the schedule is stubbed, so the only
closures in the result are the ones the weekday rule produced.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.services import branch_holiday_service, branch_hours_service


class _Result:
    def scalars(self):
        return []  # no holidays


def _db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    return db


SUN = date(2026, 9, 6)  # Sunday
FIRST_SAT = "2026-09-12"
FIRST_MON = "2026-09-07"


@pytest.mark.asyncio
async def test_closed_weekday_becomes_closed_dates(monkeypatch):
    # Open Sunday–Friday (0..5); Saturday (6) absent = closed.
    sched = {d: ("09:00", "23:00") for d in range(6)}

    async def fake_schedule(db, branch_id):
        return sched

    monkeypatch.setattr(branch_hours_service, "schedule", fake_schedule)

    closed = await branch_holiday_service.closed_dates_for(_db(), "b1", today=SUN)
    assert FIRST_SAT in closed  # a closed weekday is a closed date
    assert FIRST_MON not in closed  # an open weekday is not


@pytest.mark.asyncio
async def test_no_schedule_adds_no_weekday_closures(monkeypatch):
    async def no_schedule(db, branch_id):
        return None

    monkeypatch.setattr(branch_hours_service, "schedule", no_schedule)

    closed = await branch_holiday_service.closed_dates_for(_db(), "b1", today=SUN)
    assert closed == frozenset()  # only holidays would close it, and there are none
