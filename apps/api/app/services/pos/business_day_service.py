"""
Trading-day ("business date") handling.

A bakery that serves until 01:00 must book those sales against the day that
started at 09:00, not against the calendar date after midnight. Every operational
record therefore carries a `business_date` derived from the branch's
`business_day_start` cut-off rather than from `date.today()`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.database import SchedulerSessionFactory
from app.core.exceptions import ConflictError
from app.models.base import utcnow
from app.models.branch import Branch, BranchBusinessDay
from app.models.business_settings import BusinessSettings
from app.models.till import Till, TillStatusEnum
from app.models.user import User

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Dubai"

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 6.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4806

#: Hourly. A stranded day being closed an hour late costs nothing, and the sweep
#: is a read plus a few writes, not worth running by the minute.
_TICK_SECONDS = 3600

__all__ = [
    "business_date_for",
    "close_current",
    "current_business_date",
    "get_or_open",
    "resolve_timezone",
    "run_forever",
    "shop_today",
    "sweep_stale_business_days",
]


async def resolve_timezone(db: AsyncSession) -> ZoneInfo:
    """The business's local timezone, falling back to Gulf Standard Time."""
    settings = (
        await db.execute(select(BusinessSettings).limit(1))
    ).scalar_one_or_none()
    name = settings.timezone if settings and settings.timezone else DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def shop_today(tz: ZoneInfo | None = None) -> date:
    """
    Today's calendar date where the shop is, not where the server is.

    The containers run on UTC and Dubai is UTC+4, so `date.today()` rolls over
    at 04:00 local. Every report defaulting to "today" was therefore reading a
    UTC day: between midnight and 04:00 it showed yesterday's takings and
    disagreed with the `business_date` figures on the same screen.

    This is the plain calendar date, deliberately — the branch cut-off shift
    belongs to `business_date_for`, which is about which trading day a *sale*
    books to, not about what the date is right now.
    """
    return datetime.now(tz or ZoneInfo(DEFAULT_TIMEZONE)).date()


def _parse_cutoff(value: str) -> timedelta:
    try:
        hours, minutes = value.split(":")
        return timedelta(hours=int(hours), minutes=int(minutes))
    except (ValueError, AttributeError):
        return timedelta(hours=4)


def business_date_for(branch: Branch, moment: datetime, tz: ZoneInfo) -> str:
    """
    The trading date `moment` belongs to for `branch`.

    Anything before the branch's cut-off is still part of the previous trading
    day, so 00:30 with a 04:00 cut-off returns yesterday's date.
    """
    local = moment.astimezone(tz)
    cutoff = _parse_cutoff(branch.business_day_start)
    shifted = local - cutoff
    return shifted.date().isoformat()


async def current_business_date(db: AsyncSession, branch: Branch) -> str:
    tz = await resolve_timezone(db)
    return business_date_for(branch, utcnow(), tz)


async def range_bounds(
    db: AsyncSession, date_from: str | None, date_to: str | None
) -> tuple[datetime, datetime] | None:
    """UTC bounds for an inclusive `[date_from, date_to]` in the shop's timezone.

    The same window the dashboard aggregates over, so a date range on the orders
    list and on the dashboard mean the same days. Returns `None` when either end
    is missing — the caller then applies no date filter (an all-time list). A
    reversed pair is swapped rather than rejected.
    """
    if not (date_from and date_to):
        return None
    tz = await resolve_timezone(db)
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    start = datetime(d_from.year, d_from.month, d_from.day, tzinfo=tz).astimezone(
        timezone.utc
    )
    # The last microsecond of `to`'s local day, so `created_at <= end` owns it.
    end_local = datetime(d_to.year, d_to.month, d_to.day, tzinfo=tz) + timedelta(days=1)
    end = end_local.astimezone(timezone.utc) - timedelta(microseconds=1)
    return start, end


def next_rollover(branch: Branch, moment: datetime, tz: ZoneInfo) -> datetime:
    """
    When `branch` stops trading the day `moment` falls in.

    The counterpart to `business_date_for`: that one names the day, this one
    says when it ends. Both read the same cut-off, so "until end of day" on the
    terminal and the date an order books under can never mean different things.

    Returned in UTC because it is stored and compared as an instant. A shop with
    an 04:00 cut-off marking something out at 01:00 gets 04:00 *this* morning —
    three hours away, not twenty-seven — because 01:00 is still yesterday's
    trading day and yesterday's day ends at the next cut-off.
    """
    local = moment.astimezone(tz)
    cutoff = _parse_cutoff(branch.business_day_start)
    todays_cutoff = local.replace(hour=0, minute=0, second=0, microsecond=0) + cutoff
    rollover = (
        todays_cutoff if todays_cutoff > local else todays_cutoff + timedelta(days=1)
    )
    return rollover.astimezone(timezone.utc)


async def get_or_open(
    db: AsyncSession, branch: Branch, *, opened_by: User | None = None
) -> BranchBusinessDay:
    """
    Return the branch's open trading day, creating it on first activity.

    Idempotent: concurrent terminals opening their tills at the same moment both
    land on the same row thanks to the (branch_id, business_date) unique index.
    """
    business_date = await current_business_date(db, branch)

    existing = (
        await db.execute(
            select(BranchBusinessDay).where(
                BranchBusinessDay.branch_id == branch.id,
                BranchBusinessDay.business_date == business_date,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    day = BranchBusinessDay(
        branch_id=branch.id,
        business_date=business_date,
        opened_at=utcnow(),
        opened_by_id=opened_by.id if opened_by else None,
    )
    db.add(day)
    await db.flush()
    await db.refresh(day)
    return day


async def close_current(
    db: AsyncSession,
    branch: Branch,
    *,
    closed_by: User | None = None,
    business_date: str | None = None,
) -> BranchBusinessDay:
    """
    End of day. Refuses to run while a till is still open, because the Z-report
    totals would be incomplete the moment the remaining cashier takes a payment.

    Closes the branch's *current* trading day by default. Pass `business_date` to
    close a specific earlier day: a day whose trading has already rolled over —
    the register never reached "end of day" before the cut-off passed — could
    otherwise never be closed, because this only ever looked at the day it is
    right now (F-POS-26). The nightly `sweep_stale_business_days` uses this to
    finish those stranded days, and `closed_by` is optional so a system close can
    leave no cashier's name on it.
    """
    business_date = business_date or await current_business_date(db, branch)
    day = (
        await db.execute(
            select(BranchBusinessDay).where(
                BranchBusinessDay.branch_id == branch.id,
                BranchBusinessDay.business_date == business_date,
            )
        )
    ).scalar_one_or_none()
    if day is None:
        raise ConflictError("There is no open business day for this branch")
    if day.closed_at is not None:
        raise ConflictError(f"Business day {business_date} is already closed")

    open_tills = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Till)
                .where(
                    Till.branch_id == branch.id,
                    Till.business_date == business_date,
                    Till.status == TillStatusEnum.OPEN.value,
                )
            )
        ).scalar_one()
    )
    if open_tills:
        raise ConflictError(
            f"{open_tills} till(s) are still open — close them before end of day"
        )

    totals = await _day_totals(db, branch, business_date)
    day.total_sales = totals["net_sales"]
    day.total_orders = int(totals["orders_count"])
    day.total_discounts = totals["discounts"]
    day.total_returns = totals["returns"]
    day.total_taxes = totals["taxes"]
    day.closed_at = utcnow()
    day.closed_by_id = closed_by.id if closed_by is not None else None
    await db.flush()
    await db.refresh(day)
    return day


async def _day_totals(
    db: AsyncSession, branch: Branch, business_date: str
) -> dict[str, Decimal]:
    """
    Aggregate the day's closed tills. Till totals are frozen at close, so this
    reads them back rather than re-summing every order.
    """
    tills = list(
        (
            await db.execute(
                select(Till).where(
                    Till.branch_id == branch.id,
                    Till.business_date == business_date,
                    Till.status == TillStatusEnum.CLOSED.value,
                )
            )
        )
        .scalars()
        .all()
    )

    def _sum(key: str) -> Decimal:
        total = Decimal("0")
        for till in tills:
            raw = (till.totals or {}).get(key, 0)
            total += Decimal(str(raw))
        return total

    return {
        "orders_count": _sum("orders_count"),
        "net_sales": _sum("net_sales"),
        "discounts": _sum("discounts"),
        "returns": _sum("returns"),
        "taxes": _sum("taxes"),
    }


async def sweep_stale_business_days(
    db: AsyncSession, now: datetime | None = None
) -> list[str]:
    """
    Close every trading day whose date has already rolled past.

    A day is closed at "end of day" through `close_current`, which only ever
    looks at the branch's *current* business date. So a day nobody closed before
    the cut-off moved on — a busy Friday whose manager forgot, a register that
    was down at 04:00 — became unreachable: the next call already named the new
    day, and the old one stayed `open` forever, out of every Z-report and end-of-
    day total (F-POS-26). This finishes those stranded days.

    A day still holding an open till is left alone — its totals are not final
    yet, and it is closed the ordinary way once the till is. Everything else with
    a `business_date` earlier than today's is closed via `close_current`, with no
    cashier's name on the close. Returns `"<branch_id>:<date>"` for each day it
    closed; the caller commits.
    """
    tz = await resolve_timezone(db)
    moment = now or utcnow()
    branches = (
        (await db.execute(select(Branch).where(Branch.is_active.is_(True))))
        .scalars()
        .all()
    )
    closed: list[str] = []
    for branch in branches:
        current = business_date_for(branch, moment, tz)
        stale = (
            (
                await db.execute(
                    select(BranchBusinessDay)
                    .where(
                        BranchBusinessDay.branch_id == branch.id,
                        BranchBusinessDay.closed_at.is_(None),
                        BranchBusinessDay.business_date < current,
                    )
                    .order_by(BranchBusinessDay.business_date)
                )
            )
            .scalars()
            .all()
        )
        for day in stale:
            try:
                await close_current(db, branch, business_date=day.business_date)
                closed.append(f"{branch.id}:{day.business_date}")
            except ConflictError as exc:
                # A still-open till, or a day already closed in the gap — neither
                # is an error the sweep should raise on; leave it for the ordinary
                # close and move on.
                logger.info(
                    "business day sweep: left %s %s open — %s",
                    branch.reference,
                    day.business_date,
                    exc,
                )
    return closed


async def run_forever() -> None:
    """Hourly, close any trading day that rolled over without an end-of-day.

    Leader-elected on an advisory lock and beating its heartbeat, the same shape
    as the other lifespan loops — no cron in this stack, and one worker inside a
    sweep at a time.
    """
    logger.info("Business day sweeper started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            # Sleeps first: boot is busy and nothing here is urgent.
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("business_day_sweeper")
            async with advisory_lock.held(
                _ADVISORY_LOCK_KEY, name="business day sweeper"
            ) as mine:
                if not mine:
                    continue
                async with SchedulerSessionFactory() as db:
                    closed = await sweep_stale_business_days(db)
                    await db.commit()
                    if closed:
                        logger.info(
                            "Business day sweeper closed %s stranded day(s): %s",
                            len(closed),
                            closed,
                        )
        except asyncio.CancelledError:
            logger.info("Business day sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Business day sweeper tick failed")
