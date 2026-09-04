"""
The days a branch does not trade.

One reader, so every question about "is the shop shut that day" is answered
from the same rows in the same shape: a set of `YYYY-MM-DD` strings, which is
what `core.trading_hours` takes and what `date.isoformat()` produces. Nothing
else in the codebase should be comparing these strings by hand — a caller that
builds its own set is a caller that will one day build it from a different
window of dates and quietly disagree.

**Only the future is fetched.** A promise is about when something will arrive,
so a closure that has already passed cannot move it, and a shop with five years
of history should not be loading five years of Eids into every checkout. The
window is generous rather than tight — a third-party courier quoting several
days, from a branch closed either side of it, is still comfortably inside it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.models.branch import BranchHoliday
from app.services import branch_hours_service

__all__ = ["HORIZON_DAYS", "closed_dates_for", "listing", "shop_today"]

#: How far ahead a promise can possibly reach. A next-day courier with a
#: multi-day SLA, dispatched from a branch that is shut either side of the run,
#: is the longest chain there is and it is nowhere near this.
HORIZON_DAYS = 120


def shop_today() -> date:
    """Today's date where the shop is. The containers run on UTC."""
    return datetime.now(trading_hours.TZ).date()


async def closed_dates_for(
    db: AsyncSession, branch_id: uuid.UUID | None, *, today: date | None = None
) -> frozenset[str]:
    """
    This branch's closed days, as `YYYY-MM-DD`, from today to the horizon.

    Two kinds of closed day, one set: the explicit holidays somebody wrote down,
    and the **weekdays the branch does not trade** in its weekly schedule — a
    branch closed every Monday is closed on each Monday in the window exactly as
    if each were a holiday. Folding both here keeps this the single reader of
    "is the shop shut that day", so every consumer that already threads
    `closed_dates` through `trading_hours` becomes weekly-schedule-aware with no
    change of its own. A branch with no weekly schedule yet contributes no
    weekday closures — only its holidays close it, as before.

    Empty for a branch that is None — a zone with no branch predates zones
    knowing about branches, and inventing closures for it would be inventing
    them for the shop that has always served it.
    """
    if branch_id is None:
        return frozenset()

    start = today or shop_today()
    end = start + timedelta(days=HORIZON_DAYS)
    rows = (
        await db.execute(
            select(BranchHoliday.holiday_date).where(
                BranchHoliday.branch_id == branch_id,
                BranchHoliday.holiday_date >= start.isoformat(),
                BranchHoliday.holiday_date <= end.isoformat(),
            )
        )
    ).scalars()
    closed = set(rows)

    sched = await branch_hours_service.schedule(db, branch_id)
    if sched is not None:
        day = start
        while day <= end:
            if branch_hours_service.window_for(sched, day) is None:
                closed.add(day.isoformat())
            day += timedelta(days=1)
    return frozenset(closed)


async def listing(
    db: AsyncSession,
    branch_id: uuid.UUID,
    *,
    include_past: bool = False,
    today: date | None = None,
) -> list[BranchHoliday]:
    """
    The whole rows, earliest first, for the admin screen.

    Past closures are kept in the table — they are the record of why a promise
    once read the way it did — but the screen leads with what is still ahead,
    which is the only part anybody can still act on.
    """
    stmt = select(BranchHoliday).where(BranchHoliday.branch_id == branch_id)
    if not include_past:
        stmt = stmt.where(
            BranchHoliday.holiday_date >= (today or shop_today()).isoformat()
        )
    rows = await db.execute(stmt.order_by(BranchHoliday.holiday_date))
    return list(rows.scalars().all())
