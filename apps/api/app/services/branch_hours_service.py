"""Resolve a branch's trading window from its weekly schedule.

`branch_weekly_hours` (one shift per weekday, 0=Sunday…6=Saturday) is the single
source of truth for when a branch trades. This turns that schedule into the two
shapes the rest of the system reads, so nothing else has to learn about weekdays:

- the `(opens, closes)` window for a given local date — `effective_window()` gives
  the day's window, falling through a closed day to the next open one, which is what
  every "what are today's hours" reader (the trading-hours engine's callers, the
  storefront pickup display, the courier pickup point) feeds in; and what the
  marketplace fan-out sends per portal;
- the weekdays a branch is closed — folded by `branch_holiday_service` into the
  same `closed_dates` a one-off holiday produces, so every "is the branch open"
  consumer treats a closed weekday exactly like a closure with no new code.

A branch with **no weekly rows at all** (freshly created, or not yet filled in)
has no schedule: `schedule()` returns None and `effective_window()` returns None.
The trading-hours engine reads an unresolvable window as **always open** (an
over-optimistic promise beats a shop that silently stops quoting), so a branch is
open until its schedule is entered — the schedule is the only place hours live now.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.branch import BranchWeeklyHours

__all__ = [
    "model_weekday",
    "schedule",
    "window_for",
    "next_open_window",
    "effective_window",
    "list_weekly",
    "set_weekly",
]

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def model_weekday(day: date) -> int:
    """`day`'s weekday as `branch_weekly_hours` numbers it: 0=Sunday…6=Saturday.

    Python's `date.weekday()` is Monday=0…Sunday=6; shifting by one and wrapping
    lands Sunday on 0, which is how the UAE week — and every portal — orders days.
    """
    return (day.weekday() + 1) % 7


async def schedule(
    db: AsyncSession, branch_id: uuid.UUID | None
) -> dict[int, tuple[str, str]] | None:
    """The branch's weekly schedule as `{weekday: (opens, closes)}`, or None.

    None means the branch has no weekly rows at all — no schedule to read, so the
    caller falls back to the single window. An empty dict never happens: a branch
    with rows has at least one open weekday, and a weekday simply absent from the
    dict is closed.
    """
    if branch_id is None:
        return None
    rows = (
        (
            await db.execute(
                select(BranchWeeklyHours)
                .where(BranchWeeklyHours.branch_id == branch_id)
                .order_by(BranchWeeklyHours.weekday)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return {r.weekday: (r.opens, r.closes) for r in rows}


def window_for(
    sched: dict[int, tuple[str, str]] | None, day: date
) -> tuple[str, str] | None:
    """The `(opens, closes)` this schedule gives `day`, or None if closed that day.

    None both when the schedule is None (no schedule) and when the weekday has no
    shift (closed) — the caller has the schedule in hand to tell the two apart, and
    for stamping a window they collapse to the same "nothing to open today".
    """
    if sched is None:
        return None
    return sched.get(model_weekday(day))


def next_open_window(
    sched: dict[int, tuple[str, str]] | None, start: date, *, horizon: int = 7
) -> tuple[str, str] | None:
    """The next open day's window at or after `start`, within `horizon` days.

    Lets a reader show the hours a branch will next open on rather than a stale or
    empty window when it is closed today. `effective_window` layers this on top of
    today's own window.
    """
    if not sched:
        return None
    from datetime import timedelta

    for offset in range(horizon):
        win = sched.get(model_weekday(start + timedelta(days=offset)))
        if win is not None:
            return win
    return None


def effective_window(
    sched: dict[int, tuple[str, str]] | None, day: date
) -> tuple[str, str] | None:
    """The `(opens, closes)` to show/enforce for `day`: its own window, else the
    next open day's, else None (no schedule, or closed for the whole horizon).

    The one shape every single-window reader wants now that the derived
    `opening_from`/`opening_to` cache is gone — identical to what that cache held.
    Callers pair it with `closed_dates` from `branch_holiday_service`, which makes
    a closed `day` answer closed regardless of the window this returns.
    """
    return window_for(sched, day) or next_open_window(sched, day)


async def list_weekly(db: AsyncSession, branch_id: Any) -> list[BranchWeeklyHours]:
    """The branch's weekly shifts as rows, ordered by weekday.

    The row form (not the `{weekday: window}` dict `schedule()` returns) for the
    admin editor and the marketplace fan-out, which want the ORM objects.
    """
    return list(
        (
            await db.execute(
                select(BranchWeeklyHours)
                .where(BranchWeeklyHours.branch_id == branch_id)
                .order_by(BranchWeeklyHours.weekday)
            )
        )
        .scalars()
        .all()
    )


async def set_weekly(
    db: AsyncSession, branch_id: Any, shifts: list[dict[str, Any]]
) -> list[BranchWeeklyHours]:
    """Replace a branch's whole weekly schedule (a weekday with no shift = closed).

    Whole-list replace rather than per-row edits: a schedule is read and set as one
    thing, and diffing sub-rows would be its own bug surface. **One shift per day** —
    a second shift on a weekday is rejected, matching `uq_branch_weekly_hours_day`.
    `shift_index` is always 0 (kept only so the column stays populated).
    """
    cleaned: list[dict[str, Any]] = []
    seen: set[int] = set()
    for s in shifts:
        weekday = int(s["weekday"])
        opens = str(s["opens"])
        closes = str(s["closes"])
        if not (0 <= weekday <= 6):
            raise BadRequestError(f"weekday {weekday} out of range 0..6")
        if not _TIME_RE.match(opens) or not _TIME_RE.match(closes):
            raise BadRequestError(f"times must be HH:MM, got {opens}-{closes}")
        if weekday in seen:
            raise BadRequestError(
                f"weekday {weekday} has more than one shift — one shift per day"
            )
        seen.add(weekday)
        cleaned.append(
            {"weekday": weekday, "shift_index": 0, "opens": opens, "closes": closes}
        )

    for row in await list_weekly(db, branch_id):
        await db.delete(row)
    await db.flush()
    for c in cleaned:
        db.add(BranchWeeklyHours(branch_id=branch_id, **c))
    await db.flush()
    return await list_weekly(db, branch_id)
