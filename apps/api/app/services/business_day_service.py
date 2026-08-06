"""
Trading-day ("business date") handling.

A bakery that serves until 01:00 must book those sales against the day that
started at 09:00, not against the calendar date after midnight. Every operational
record therefore carries a `business_date` derived from the branch's
`business_day_start` cut-off rather than from `date.today()`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.base import utcnow
from app.models.branch import Branch, BranchBusinessDay
from app.models.business_settings import BusinessSettings
from app.models.till import Till, TillStatusEnum
from app.models.user import User

DEFAULT_TIMEZONE = "Asia/Dubai"

__all__ = [
    "business_date_for",
    "close_current",
    "current_business_date",
    "get_or_open",
    "resolve_timezone",
    "shop_today",
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
    db: AsyncSession, branch: Branch, *, closed_by: User
) -> BranchBusinessDay:
    """
    End of day. Refuses to run while a till is still open, because the Z-report
    totals would be incomplete the moment the remaining cashier takes a payment.
    """
    business_date = await current_business_date(db, branch)
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
    day.closed_by_id = closed_by.id
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


def today_iso(tz: ZoneInfo) -> date:
    return utcnow().astimezone(tz).date()
