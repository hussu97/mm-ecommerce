"""Daily demand facts: what each branch sold of each produced good, by clock hour,
and how many of each hour's open minutes it had stock.

**Sales** are completed sales on every channel (the reports' `_COMPLETED_SALE`),
expanded through the *current* active recipes to produced-good units with the
same per-owner requirements the auto-availability sweep uses. That gives one
consistent series back to the first order, long before the ledger started
recording consumption. A product with ``consumes_stock=False`` draws through its
options only, as ``recipe_service.snapshot_order`` does. A single order taking
more than ``BULK_ORDER_UNITS`` of one item is a one-off, kept out of demand.

**Stock** is replayed from the ledger: the balance at the start of the business
day plus every closed movement, in the order it happened (``occurred_at``). The
shift report records the day's production at till close, after midnight, though
it was on the shelf from the evening; a same-day category's production is
therefore placed at the ready time. A branch's stock is only known from its first
physical count (or opening balance) — before that the in-stock minutes are NULL,
not a guess.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch, BranchHoliday
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import InventoryItemKindEnum, RecipeOwnerKindEnum
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.replenishment import ReplenishmentDailyFact
from app.services import branch_hours_service
from app.services.inventory import auto_availability_service, recipe_service
from app.services.inventory.replenishment import engine
from app.services.inventory.replenishment.settings import load_settings
from app.services.pos import business_day_service
from app.services.pos.pos_reports._base import _COMPLETED_SALE

logger = logging.getLogger(__name__)

#: One order taking more than this of one produced good is a one-off (a party
#: order), not everyday demand.
BULK_ORDER_UNITS = 48.0

_PRODUCT = RecipeOwnerKindEnum.PRODUCT.value
_OPTION = RecipeOwnerKindEnum.MODIFIER_OPTION.value
_PRODUCTION = InventoryTransactionTypeEnum.PRODUCTION.value
_KNOWN_FROM = (
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
    InventoryTransactionTypeEnum.OPENING_BALANCE.value,
)
_CLOSED = TransactionStatusEnum.CLOSED.value


@dataclass
class Context:
    """Everything a day's build reads that does not change from day to day."""

    tz: ZoneInfo
    requirements: dict[tuple[str, uuid.UUID], dict[uuid.UUID, Decimal]]
    non_consuming: set[uuid.UUID]
    items: dict[uuid.UUID, uuid.UUID | None]  # produced good → category
    branches: list[Branch]
    calendars: dict[uuid.UUID, engine.BranchCalendar]
    stock_known_from: dict[uuid.UUID, datetime]
    ready_time: time
    next_day_categories: frozenset[uuid.UUID]


async def load_context(db: AsyncSession) -> Context:
    catalog = await recipe_service.load_active_catalog(db)
    requirements = auto_availability_service.requirements_by_owner(catalog)
    non_consuming = set(
        (await db.execute(select(Product.id).where(Product.consumes_stock.is_(False))))
        .scalars()
        .all()
    )
    items = {
        item_id: category_id
        for item_id, category_id in (
            await db.execute(
                select(InventoryItem.id, InventoryItem.category_id).where(
                    InventoryItem.kind == InventoryItemKindEnum.PRODUCED_GOOD.value,
                    InventoryItem.deleted_at.is_(None),
                )
            )
        ).all()
    }
    branches = list(
        (
            await db.execute(
                select(Branch).where(
                    Branch.deleted_at.is_(None), Branch.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    calendars = await branch_calendars(db, branches)
    known = {
        branch_id: first
        for branch_id, first in (
            await db.execute(
                select(
                    InventoryTransaction.branch_id,
                    func.min(
                        func.coalesce(
                            InventoryTransaction.occurred_at,
                            InventoryTransaction.posted_at,
                        )
                    ),
                )
                .where(
                    InventoryTransaction.status == _CLOSED,
                    InventoryTransaction.type.in_(_KNOWN_FROM),
                )
                .group_by(InventoryTransaction.branch_id)
            )
        ).all()
        if first is not None
    }
    settings = await load_settings(db)
    return Context(
        tz=await business_day_service.resolve_timezone(db),
        requirements=requirements,
        non_consuming=non_consuming,
        items=items,
        branches=branches,
        calendars=calendars,
        stock_known_from=known,
        ready_time=settings.same_day_ready_time,
        next_day_categories=frozenset(settings.next_day_category_ids or ()),
    )


def day_start_hour(branch: Branch) -> int:
    """The hour the branch's business day rolls over (`business_day_start`)."""
    try:
        return int(str(branch.business_day_start or "04:00").split(":")[0])
    except ValueError:
        return 4


async def branch_calendars(
    db: AsyncSession, branches: list[Branch]
) -> dict[uuid.UUID, engine.BranchCalendar]:
    """Each branch's weekly schedule and every holiday it has written down."""
    out = {}
    for branch in branches:
        holidays = (
            await db.execute(
                select(BranchHoliday.holiday_date).where(
                    BranchHoliday.branch_id == branch.id
                )
            )
        ).scalars()
        out[branch.id] = engine.BranchCalendar(
            weekly=await branch_hours_service.schedule(db, branch.id),
            closed_dates=frozenset(date.fromisoformat(d) for d in holidays),
            day_start_hour=day_start_hour(branch),
        )
    return out


def _local(ctx: Context, day: date, minutes: float) -> datetime:
    """Minutes from `day`'s local midnight as an aware UTC moment."""
    naive = datetime.combine(day, time()) + timedelta(minutes=minutes)
    return naive.replace(tzinfo=ctx.tz).astimezone(timezone.utc)


def open_minutes_by_hour(calendar: engine.BranchCalendar, day: date) -> list[int]:
    out = [0] * 24
    window = calendar.window(day)
    if window is None:
        return out
    for hour in range(24):
        lo, hi = calendar.hour_range(hour)
        out[hour] = int(max(0, min(hi, window[1]) - max(lo, window[0])))
    return out


# ─── Sales ────────────────────────────────────────────────────────────────────


async def _sales(
    db: AsyncSession, ctx: Context, day: date
) -> tuple[
    dict[tuple[uuid.UUID, uuid.UUID], list[float]],
    dict[tuple[uuid.UUID, uuid.UUID], float],
    dict[uuid.UUID, float],
]:
    """(branch, item) → units by local clock hour; (branch, item) → bulk units;
    branch → share of sale lines that expanded to nothing."""
    rows = (
        await db.execute(
            select(
                Order.id,
                Order.branch_id,
                Order.created_at,
                OrderItem.product_id,
                OrderItem.quantity,
                OrderItem.returned_quantity,
                OrderItem.selected_options_snapshot,
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.is_pos.is_(True),
                Order.business_date == day.isoformat(),
                _COMPLETED_SALE,
                OrderItem.status.is_distinct_from("void"),
            )
        )
    ).all()

    per_order: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], list[float]] = defaultdict(
        lambda: [0.0] * 24
    )
    lines: dict[uuid.UUID, int] = defaultdict(int)
    missed: dict[uuid.UUID, int] = defaultdict(int)
    for order_id, branch_id, created_at, product_id, qty, returned, options in rows:
        lines[branch_id] += 1
        billable = max(float(qty or 0) - float(returned or 0), 0.0)
        if billable <= 0:
            continue
        hour = created_at.astimezone(ctx.tz).hour
        # Whether any owner on the line has an active recipe to expand.
        known = False
        owners: list[tuple[str, uuid.UUID, float]] = []
        if product_id is not None and product_id not in ctx.non_consuming:
            owners.append((_PRODUCT, product_id, billable))
        for option in options or []:
            raw = option.get("modifier_option_id") if isinstance(option, dict) else None
            if not raw:
                continue
            try:
                option_id = uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
            owners.append(
                (_OPTION, option_id, billable * float(option.get("quantity", 1) or 1))
            )
        for kind, owner_id, multiplier in owners:
            needs = ctx.requirements.get((kind, owner_id))
            if needs is None:
                continue
            known = True
            for item_id, per_sale in needs.items():
                if item_id not in ctx.items:
                    continue
                per_order[(order_id, branch_id, item_id)][hour] += (
                    float(per_sale) * multiplier
                )
        # Unmapped aggregator line, or a stock-drawing product with no recipe.
        if product_id is None or (product_id not in ctx.non_consuming and not known):
            missed[branch_id] += 1

    hourly: dict[tuple[uuid.UUID, uuid.UUID], list[float]] = defaultdict(
        lambda: [0.0] * 24
    )
    bulk: dict[tuple[uuid.UUID, uuid.UUID], float] = defaultdict(float)
    for (_, branch_id, item_id), by_hour in per_order.items():
        if sum(by_hour) > BULK_ORDER_UNITS:
            bulk[(branch_id, item_id)] += sum(by_hour)
            continue
        target = hourly[(branch_id, item_id)]
        for hour, units in enumerate(by_hour):
            target[hour] += units
    unexpanded = {b: missed[b] / lines[b] for b in lines if lines[b]}
    return hourly, bulk, unexpanded


# ─── Stock ────────────────────────────────────────────────────────────────────


def _effective_expr():
    return func.coalesce(
        InventoryTransaction.occurred_at, InventoryTransaction.posted_at
    )


async def _stock(
    db: AsyncSession,
    ctx: Context,
    day: date,
    branch: Branch,
) -> tuple[dict[uuid.UUID, list[int]], dict[uuid.UUID, float]] | None:
    """item → in-stock open minutes by clock hour, and item → closing on hand.
    None when the branch's stock was not known for the whole day."""
    calendar = ctx.calendars[branch.id]
    known_from = ctx.stock_known_from.get(branch.id)
    window = calendar.window(day)
    day_start = _local(ctx, day, calendar.day_start_hour * 60)
    day_end = day_start + timedelta(days=1)
    open_start = _local(ctx, day, window[0]) if window else day_start
    if known_from is None or known_from > open_start:
        return None

    item_ids = list(ctx.items)
    effective = _effective_expr()
    before = {
        item_id: float(total or 0)
        for item_id, total in (
            await db.execute(
                select(
                    InventoryTransactionItem.item_id,
                    func.sum(InventoryTransactionItem.signed_quantity),
                )
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryTransactionItem.transaction_id,
                )
                .where(
                    InventoryTransaction.status == _CLOSED,
                    InventoryTransaction.branch_id == branch.id,
                    InventoryTransactionItem.item_id.in_(item_ids),
                    effective < day_start,
                    # A production booked to today after the day started is
                    # replayed below at its ready time instead.
                    ~and_(
                        InventoryTransaction.type == _PRODUCTION,
                        InventoryTransaction.business_date == day.isoformat(),
                        effective >= day_start,
                    ),
                )
                .group_by(InventoryTransactionItem.item_id)
            )
        ).all()
    }
    moves = (
        await db.execute(
            select(
                InventoryTransactionItem.item_id,
                InventoryTransactionItem.signed_quantity,
                effective,
                InventoryTransaction.type,
                InventoryTransaction.business_date,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryTransactionItem.transaction_id,
            )
            .where(
                InventoryTransaction.status == _CLOSED,
                InventoryTransaction.branch_id == branch.id,
                InventoryTransactionItem.item_id.in_(item_ids),
                or_(
                    and_(effective >= day_start, effective < day_end),
                    and_(
                        InventoryTransaction.type == _PRODUCTION,
                        InventoryTransaction.business_date == day.isoformat(),
                        effective >= day_end,
                    ),
                ),
            )
        )
    ).all()

    ready = (
        datetime.combine(day, ctx.ready_time)
        .replace(tzinfo=ctx.tz)
        .astimezone(timezone.utc)
    )
    timeline: dict[uuid.UUID, list[tuple[datetime, float]]] = defaultdict(list)
    for item_id, signed, when, kind, business_date in moves:
        if (
            kind == _PRODUCTION
            and business_date == day.isoformat()
            and ctx.items.get(item_id) not in ctx.next_day_categories
            and when > ready
        ):
            when = ready
        timeline[item_id].append((when, float(signed or 0)))

    hours = [
        (hour, _local(ctx, day, lo), _local(ctx, day, hi))
        for hour in range(24)
        for lo, hi in [calendar.hour_range(hour)]
    ]
    open_by_hour = open_minutes_by_hour(calendar, day)
    open_lo = _local(ctx, day, window[0]) if window else day_start
    open_hi = _local(ctx, day, window[1]) if window else day_start

    in_stock: dict[uuid.UUID, list[int]] = {}
    closing: dict[uuid.UUID, float] = {}
    for item_id in item_ids:
        balance = before.get(item_id, 0.0)
        events = sorted(timeline.get(item_id, []), key=lambda e: e[0])
        # Stock intervals: (start, end, balance) across the business day.
        intervals: list[tuple[datetime, datetime, float]] = []
        cursor = day_start
        for when, signed in events:
            when = min(max(when, day_start), day_end)
            if when > cursor:
                intervals.append((cursor, when, balance))
                cursor = when
            balance += signed
        intervals.append((cursor, day_end, balance))
        closing[item_id] = balance
        minutes = [0] * 24
        for hour, lo, hi in hours:
            if not open_by_hour[hour]:
                continue
            lo, hi = max(lo, open_lo), min(hi, open_hi)
            total = 0.0
            for start, end, level in intervals:
                if level <= 0:
                    continue
                overlap = (min(end, hi) - max(start, lo)).total_seconds() / 60
                if overlap > 0:
                    total += overlap
            minutes[hour] = min(int(round(total)), open_by_hour[hour])
        in_stock[item_id] = minutes
    return in_stock, closing


# ─── Build ────────────────────────────────────────────────────────────────────


async def build_day(db: AsyncSession, day: date, ctx: Context | None = None) -> int:
    """(Re)build every branch × produced-good fact for one business day.
    Idempotent: the day's rows are replaced. Flushes; the caller commits."""
    ctx = ctx or await load_context(db)
    hourly, bulk, unexpanded = await _sales(db, ctx, day)
    await db.execute(
        delete(ReplenishmentDailyFact).where(
            ReplenishmentDailyFact.business_date == day
        )
    )
    now = datetime.now(timezone.utc)
    written = 0
    for branch in ctx.branches:
        calendar = ctx.calendars[branch.id]
        opened = open_minutes_by_hour(calendar, day)
        sold_here = any(key[0] == branch.id for key in hourly) or any(
            key[0] == branch.id for key in bulk
        )
        if not sum(opened) and not sold_here:
            continue
        stock = await _stock(db, ctx, day, branch)
        for item_id in ctx.items:
            units = hourly.get((branch.id, item_id), [0.0] * 24)
            db.add(
                ReplenishmentDailyFact(
                    branch_id=branch.id,
                    item_id=item_id,
                    business_date=day,
                    sales_units=Decimal(str(round(sum(units), 4))),
                    bulk_units=Decimal(
                        str(round(bulk.get((branch.id, item_id), 0.0), 4))
                    ),
                    hourly_units=[Decimal(str(round(u, 4))) for u in units],
                    hourly_open_minutes=opened,
                    hourly_in_stock_minutes=stock[0][item_id] if stock else None,
                    closing_on_hand=(
                        Decimal(str(round(stock[1][item_id], 4))) if stock else None
                    ),
                    unexpanded_line_share=Decimal(
                        str(round(unexpanded.get(branch.id, 0.0), 4))
                    ),
                    built_at=now,
                )
            )
            written += 1
    await db.flush()
    return written


async def first_sale_date(db: AsyncSession) -> date | None:
    first = await db.scalar(
        select(func.min(Order.business_date)).where(
            Order.is_pos.is_(True), Order.business_date.is_not(None), _COMPLETED_SALE
        )
    )
    return date.fromisoformat(first) if first else None


async def missing_days(db: AsyncSession, start: date, end: date) -> list[date]:
    """Days in [start, end] with no facts at all."""
    have = set(
        (
            await db.execute(
                select(ReplenishmentDailyFact.business_date)
                .where(
                    ReplenishmentDailyFact.business_date >= start,
                    ReplenishmentDailyFact.business_date <= end,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    out = []
    day = start
    while day <= end:
        if day not in have:
            out.append(day)
        day += timedelta(days=1)
    return out
