"""
The admin home dashboard: one live read of the current trading day.

The console's home page used to compute "today's orders" and "today's revenue"
in the browser from the last ten orders it happened to have loaded — so the
figures were capped at ten and the revenue was summed client-side, against the
rule that money is quantised once, server-side (CLAUDE.md rule #10). This is
that number done properly: every order of the shop's local day, across the
storefront, the registers and the aggregators, aggregated in one place.

"Today" is the shop's calendar day in its own timezone, resolved to explicit
UTC bounds rather than `func.date(created_at)` — the latter dates a stored UTC
instant by the database's timezone and, near midnight in the Gulf, books the
first four hours of the day to yesterday (see `business_day_service.shop_today`).

Deliberately uncached: it is the one screen an admin keeps open to watch the day
move, and five-minute-stale headline figures read as a bug, not a saving.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, Text, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.money import money
from app.core.permissions import require
from app.models import (
    Branch,
    Courier,
    CustomOrder,
    CustomOrderStatusEnum,
    DeliveryMethodEnum,
    InventoryItem,
    InventoryLevel,
    Order,
    OrderSourceEnum,
    OrderStatusEnum,
    PurchaseOrder,
    PurchaseOrderStatusEnum,
    Till,
    TillStatusEnum,
    User,
)
from app.models.base import utcnow
from app.models.category import Category
from app.models.legal_entity import LegalEntity
from app.models.order import OrderItem
from app.models.order_delivery import OrderDelivery
from app.models.payment_method import PaymentMethod
from app.models.pos_order import OrderItemStatusEnum, OrderPayment
from app.models.product import Product
from app.schemas.dashboard import (
    BreakdownRow,
    CourierBreakdownRow,
    DashboardOps,
    DashboardSummary,
    DashboardTodayResponse,
    HeatmapCell,
    SeriesPoint,
)
from app.services.couriers import courier_catalog
from app.services.orders import order_query
from app.services.pos import business_day_service

router = APIRouter()

#: A cancelled order is a non-event for takings — no money changed hands and no
#: work is owed. It stays out of every revenue figure and every channel mix, but
#: is still shown in `by_status` so the day's cancellations are visible.
_REVENUE_STATUSES = Order.status != OrderStatusEnum.CANCELLED

#: The trading pipeline, most human-readable label first. Kept explicit so the
#: order the buckets appear in on screen is decided here, not by row order.
#:
#: The storefront (`online`) is split in two: a store-pickup order is its own
#: channel ("Store Pickup") rather than folded into the website's delivery sales,
#: because the shop tracks the two as different businesses. The split is by
#: `delivery_method`, expressed in `_CHANNEL_EXPR` below — the counter and the
#: marketplaces group on `source` unchanged.
_CHANNEL_LABELS = {
    "website_delivery": "Website Delivery",
    "website_pickup": "Store Pickup",
    OrderSourceEnum.CASHIER.value: "Counter",
    OrderSourceEnum.AGGREGATOR.value: "Aggregator",
}

#: The grouping key for `by_channel`: `online` orders split into
#: `website_delivery` / `website_pickup` by fulfilment method; everything else
#: keeps its `source`. Mirrors `order_query.WEBSITE_PICKUP_CODE`.
_CHANNEL_EXPR = case(
    (
        and_(
            Order.source == OrderSourceEnum.ONLINE.value,
            Order.delivery_method == DeliveryMethodEnum.PICKUP,
        ),
        "website_pickup",
    ),
    (Order.source == OrderSourceEnum.ONLINE.value, "website_delivery"),
    else_=Order.source,
)


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


def _status_clause(statuses: list[str] | None):
    """The status filter for the revenue figures.

    With an explicit selection (the dashboard's status multi-select), the figures
    narrow to exactly those statuses — including cancelled, if the operator picked
    it, since they asked to see it. With no selection, the default excludes
    cancellations from every revenue/mix figure (no money changed hands), which is
    what the day's takings have always meant.
    """
    if statuses:
        return Order.status.in_(statuses)
    return _REVENUE_STATUSES


def _branch_clause(branch_ids):
    """Narrow to a set of fulfilling branches, or nothing when none are picked."""
    return Order.branch_id.in_(branch_ids) if branch_ids else None


def _legal_entity_clause(legal_entity_ids):
    """Narrow to a set of legal entities the order was billed under."""
    return Order.legal_entity_id.in_(legal_entity_ids) if legal_entity_ids else None


def _filters(
    statuses, couriers, branch_ids=None, legal_entity_ids=None, category_ids=None
):
    """The status, courier, branch, legal-entity and category where-clauses shared
    by every windowed figure. Branch and legal entity are plain `IN`s over a
    column on the order; category is an EXISTS over the order's lines (an order
    can span several), applied only when a set is picked."""
    clauses = [_status_clause(statuses)]
    for extra in (
        order_query.courier_clause(couriers),
        _branch_clause(branch_ids),
        _legal_entity_clause(legal_entity_ids),
        order_query.category_clause(category_ids),
    ):
        if extra is not None:
            clauses.append(extra)
    return clauses


async def _breakdown(
    db: AsyncSession,
    column,
    *,
    start,
    end,
    labels=None,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[BreakdownRow]:
    """Orders and revenue grouped by `column` over the window, revenue-eligible only."""
    rows = (
        await db.execute(
            select(
                column,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
            .group_by(column)
            .order_by(func.count(Order.id).desc())
        )
    ).all()
    out: list[BreakdownRow] = []
    for value, count, revenue in rows:
        raw = getattr(value, "value", value)
        label = (labels or {}).get(
            raw, str(raw).replace("_", " ").title() if raw else "Unknown"
        )
        out.append(
            BreakdownRow(
                label=label,
                orders=int(count),
                revenue=float(money(revenue)),
                # The raw group value, so a selector breakdown (branch, legal
                # entity) can toggle its filter by id. Ignored by the mixes whose
                # cards are not selectors.
                code=str(raw) if raw is not None else None,
            )
        )
    return out


#: Human labels for the payment breakdown, by lower-cased tender key. Unlisted
#: values fall back to their title-cased raw string, so a new processor still
#: shows rather than vanishing.
_PAYMENT_LABELS = {
    "card": "Card",
    "cod": "Cash on delivery",
    "cash": "Cash",
    "online": "Online",
    "other": "Other",
}


async def _payment_breakdown(
    db: AsyncSession,
    *,
    start,
    end,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[BreakdownRow]:
    """Orders and revenue by payment method, with split counter sales expanded.

    A counter sale paid part cash, part card stamps `payment_method = "mixed"`; a
    single "Mixed" slice hides the real money, so those orders are dropped from
    the scalar grouping and re-added from their actual `order_payments` tenders
    (grouped by tender type, refunds excluded so cash + card equals the order
    total). Every other order — online and gateway included — keeps its scalar
    attribution on `Order.total`, exactly as before.
    """
    scalar_rows = (
        await db.execute(
            select(
                Order.payment_method,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                # NULL-safe: a plain `!= "mixed"` is NULL for an order with no
                # payment_method and would drop it; is_distinct_from keeps it.
                Order.payment_method.is_distinct_from("mixed"),
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
            .group_by(Order.payment_method)
        )
    ).all()

    mixed_rows = (
        await db.execute(
            select(
                PaymentMethod.type,
                func.count(func.distinct(OrderPayment.order_id)),
                func.coalesce(func.sum(OrderPayment.amount), 0),
            )
            .select_from(OrderPayment)
            .join(Order, Order.id == OrderPayment.order_id)
            .join(PaymentMethod, PaymentMethod.id == OrderPayment.payment_method_id)
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                Order.payment_method == "mixed",
                OrderPayment.is_refund.is_(False),
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
            .group_by(PaymentMethod.type)
        )
    ).all()

    totals: dict[str, list] = {}
    for value, count, revenue in list(scalar_rows) + list(mixed_rows):
        raw = getattr(value, "value", value)
        key = str(raw).lower() if raw else "unknown"
        bucket = totals.setdefault(key, [0, 0.0])
        bucket[0] += int(count)
        bucket[1] += float(money(revenue))

    out = [
        BreakdownRow(
            label=_PAYMENT_LABELS.get(key, key.replace("_", " ").title()),
            orders=orders,
            revenue=revenue,
        )
        for key, (orders, revenue) in totals.items()
    ]
    out.sort(key=lambda b: b.orders, reverse=True)
    return out


async def _window_totals(
    db: AsyncSession,
    *,
    start,
    end,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> tuple[int, float]:
    """(orders, revenue) for revenue-eligible orders created in the window."""
    result = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            ).where(
                Order.created_at >= start,
                Order.created_at <= end,
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
        )
    ).one()
    return int(result[0]), float(money(result[1]))


async def _series(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    tz_name: str,
    granularity: str,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[SeriesPoint]:
    """Orders and revenue over time, one point per interval across the window.

    Buckets on the shop's local clock (`created_at` is UTC, so it is shifted into
    the shop timezone before truncating) — by hour for the live day or a
    single-day range, by day for a multi-day range. Follows the same
    status/courier selection as every other figure, so the trend line moves with
    the page filters. Every interval in the window is emitted, zero-filled, so the
    line is continuous even on a quiet hour or day.
    """
    # `tz_name`/`granularity` are cast to text so asyncpg sends typed params —
    # an untyped bind leaves PG unable to resolve the overloaded timezone()/
    # date_trunc() signatures and the query fails at runtime.
    local = func.timezone(cast(tz_name, Text), Order.created_at)
    bucket = func.date_trunc(cast(granularity, Text), local)
    rows = (
        await db.execute(
            select(
                bucket.label("b"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
            .group_by("b")
            .order_by("b")
        )
    ).all()
    found = {b: (int(c), float(money(r))) for b, c, r in rows}

    tz = ZoneInfo(tz_name)
    start_local = start.astimezone(tz).replace(tzinfo=None)
    end_local = end.astimezone(tz).replace(tzinfo=None)
    if granularity == "hour":
        cur = start_local.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
    else:
        cur = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)

    points: list[SeriesPoint] = []
    while cur <= end_local:
        orders, revenue = found.get(cur, (0, 0.0))
        points.append(
            SeriesPoint(bucket=cur.isoformat(), orders=orders, revenue=revenue)
        )
        cur += step
    return points


async def _heatmap(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    tz_name: str,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[HeatmapCell]:
    """Orders and revenue by shop-local day-of-week × hour-of-day over the window.

    Answers "which day and hour do we sell the most" by collapsing every matching
    order in the window onto a 7×24 grid. `created_at` is UTC, so it is shifted
    into the shop timezone before the day-of-week and hour are extracted — the
    same local-clock treatment `_series` uses, so the grid and the trend line are
    the same orders counted two ways. Follows the page's status/courier selection.
    Sparse: only cells with at least one order are returned; the client zero-fills
    the rest. `extract()` yields double precision, cast to int for clean params.
    """
    local = func.timezone(cast(tz_name, Text), Order.created_at)
    dow = cast(func.extract("dow", local), Integer)
    hour = cast(func.extract("hour", local), Integer)
    rows = (
        await db.execute(
            select(
                dow.label("d"),
                hour.label("h"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                *_filters(
                    statuses, couriers, branch_ids, legal_entity_ids, category_ids
                ),
            )
            .group_by("d", "h")
            .order_by("d", "h")
        )
    ).all()
    return [
        HeatmapCell(dow=int(d), hour=int(h), orders=int(c), revenue=float(money(r)))
        for d, h, c, r in rows
    ]


async def _by_branch(
    db: AsyncSession,
    *,
    start,
    end,
    statuses=None,
    couriers=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[BreakdownRow]:
    """Orders and revenue per branch over the window, revenue-eligible only.

    `orders.branch_id` is NOT NULL — every order, storefront or counter or
    aggregator, is resolved to a branch (its pickup branch, or the branch its
    delivery zone belongs to) before it is written — so there is no "Unknown"
    bucket to explain, and every id groups to a real name.

    This is the branch *selector*, so it shows the full branch spread regardless
    of the branch filter (it never applies `branch_ids`), but it follows every
    other dimension — status, carrier and legal entity — so a filtered dashboard
    narrows the branch menu the same way the status menu narrows.
    """
    labels = {
        b_id: name
        for b_id, name in (await db.execute(select(Branch.id, Branch.name))).all()
    }
    return await _breakdown(
        db,
        Order.branch_id,
        start=start,
        end=end,
        labels=labels,
        statuses=statuses,
        couriers=couriers,
        legal_entity_ids=legal_entity_ids,
        category_ids=category_ids,
    )


async def _by_legal_entity(
    db: AsyncSession,
    *,
    start,
    end,
    statuses=None,
    couriers=None,
    branch_ids=None,
    category_ids=None,
) -> list[BreakdownRow]:
    """Orders and revenue per legal entity over the window, revenue-eligible only.

    The legal-entity *selector*, mirroring `_by_branch`: it shows the full spread
    of entities (so it never applies `legal_entity_ids`) but follows status,
    carrier and branch. `orders.legal_entity_id` is nullable — an order billed
    under no registered entity (a non-VAT counter) groups into an "Unknown"
    bucket with a null code, which the selector renders but cannot toggle.
    """
    labels = {
        e_id: name
        for e_id, name in (
            await db.execute(select(LegalEntity.id, LegalEntity.brand_name))
        ).all()
    }
    return await _breakdown(
        db,
        Order.legal_entity_id,
        start=start,
        end=end,
        labels=labels,
        statuses=statuses,
        couriers=couriers,
        branch_ids=branch_ids,
        category_ids=category_ids,
    )


async def _by_category(
    db: AsyncSession,
    *,
    start,
    end,
    statuses=None,
    couriers=None,
    branch_ids=None,
    legal_entity_ids=None,
) -> list[BreakdownRow]:
    """Orders and revenue per product category over the window, item-level.

    Unlike the order-level selectors, a category lives on the *line*: an order
    can span several categories, so revenue here is the sum of the matching
    order lines' `total_price` (VAT-inclusive, like `Order.total`) and `orders`
    is the distinct count of orders that touched the category — the two do not
    add up to the headline the way the order-level mixes do, by design. Voided
    counter lines (`status = 'void'`) are excluded; an off-counter line has a
    NULL status, so the guard is `is_distinct_from('void')`. A line whose product
    has no category — or no product at all (an open item, a deleted product) —
    groups into an un-clickable "Uncategorised" bucket with a null code.

    A selector like the others: it shows the full category spread (never applies
    its own `category_ids`) but follows status, carrier, branch and legal entity,
    so a filtered dashboard narrows the category menu the same way.
    """
    revenue = func.coalesce(func.sum(OrderItem.total_price), 0)
    rows = (
        await db.execute(
            select(
                Category.id,
                Category.name,
                func.count(func.distinct(Order.id)),
                revenue,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(Product, Product.id == OrderItem.product_id)
            .outerjoin(Category, Category.id == Product.category_id)
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                OrderItem.status.is_distinct_from(OrderItemStatusEnum.VOID.value),
                *_filters(statuses, couriers, branch_ids, legal_entity_ids),
            )
            .group_by(Category.id, Category.name)
            .order_by(revenue.desc())
        )
    ).all()
    return [
        BreakdownRow(
            label=name or "Uncategorised",
            orders=int(count),
            revenue=float(money(rev)),
            code=str(cid) if cid is not None else None,
        )
        for cid, name, count, rev in rows
    ]


async def _by_courier(
    db: AsyncSession,
    *,
    start,
    end,
    branch_ids=None,
    legal_entity_ids=None,
    category_ids=None,
) -> list[CourierBreakdownRow]:
    """Active and completed orders and revenue per carrier over the window.

    Grouped in Python via `order_query.courier_code_for` rather than in SQL,
    because "which courier" spans three different columns (source for the
    counter, `aggregator_channel` for a marketplace, the delivery record's
    provider for a dispatch courier) and there is no single column to group by.
    Counts every live or completed order — everything but the terminal set
    (cancelled/payment_failed/refunded/disputed) — not delivered-only, so a
    courier's column reflects its whole in-flight and settled load. One row per
    known courier code, busiest first. A store-pickup order has no carrier but is
    its own synthetic code (`website_pickup`), the way the counter is; an order
    with no resolvable carrier at all counts under none.
    """
    provider = (
        select(OrderDelivery.provider)
        .where(OrderDelivery.order_id == Order.id)
        .limit(1)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                Order.source,
                Order.aggregator_channel,
                Order.total,
                Order.delivery_method,
                provider.label("provider"),
                # Every VAT-inclusive fee stamped on the order. Summed uniformly:
                # each is null/zero on the channels it does not apply to, so one
                # sum yields the right composition per carrier — commission +
                # cancellation + marketing for an aggregator, the delivery charge
                # for a website courier, and the payment fee on any card/prepaid
                # order whatever the carrier.
                Order.aggregator_fee,
                Order.cancellation_fee,
                Order.marketing_fee,
                Order.delivery_fee,
                Order.payment_fee,
            ).where(
                Order.created_at >= start,
                Order.created_at <= end,
                order_query.active_or_fulfilled_clause(),
                *(
                    c
                    for c in (
                        _branch_clause(branch_ids),
                        _legal_entity_clause(legal_entity_ids),
                        order_query.category_clause(category_ids),
                    )
                    if c is not None
                ),
            )
        )
    ).all()

    # [orders, revenue, fees] per code.
    totals: dict[str, list] = {
        code: [0, 0.0, 0.0] for code in order_query.ALL_COURIER_CODES
    }
    for (
        source,
        channel,
        total,
        method,
        prov,
        aggregator_fee,
        cancellation_fee,
        marketing_fee,
        delivery_fee,
        payment_fee,
    ) in rows:
        code = order_query.courier_code_for(
            getattr(source, "value", source),
            channel,
            prov,
            delivery_method=getattr(method, "value", method),
        )
        if code is None or code not in totals:
            continue
        totals[code][0] += 1
        totals[code][1] += float(total or 0)
        # null ≠ 0 for the order economics, but for a windowed rate an unscraped
        # statement simply contributes nothing yet (documented on the schema).
        totals[code][2] += (
            float(aggregator_fee or 0)
            + float(cancellation_fee or 0)
            + float(marketing_fee or 0)
            + float(delivery_fee or 0)
            + float(payment_fee or 0)
        )

    out = [
        CourierBreakdownRow(
            code=code,
            label=order_query.courier_label(code),
            logo_url=(
                None
                if code in (order_query.COUNTER_CODE, order_query.WEBSITE_PICKUP_CODE)
                else courier_catalog.logo_url_for(code)
            ),
            orders=orders,
            revenue=float(money(revenue)),
            fee_rate=(float(money(fees / revenue * 100)) if revenue > 0 else None),
        )
        for code, (orders, revenue, fees) in totals.items()
        if orders > 0
    ]
    out.sort(key=lambda r: r.orders, reverse=True)
    return out


def _growth(current: float, prior: float) -> float:
    """Percentage change, guarding the "off nothing" case that has no rate."""
    if prior <= 0:
        return 0.0
    return round((current - prior) / prior * 100, 1)


async def _range_bounds(
    db: AsyncSession, date_from: str | None, date_to: str | None
) -> tuple[date, str | None, str, datetime, datetime, datetime, datetime]:
    """Resolve the window to aggregate over, and the prior window to grow against.

    Returns `(from_date, to_date, tz_name, start, end, prior_start, prior_end)`.

    With no dates it is the live trading day exactly as before — midnight-to-now,
    grown against the same elapsed clock window yesterday, `to_date` None. With a
    range it is [from 00:00, to 23:59:59.999999] in the shop's timezone, grown
    against the immediately-preceding window of the same number of days.
    """
    tz = await business_day_service.resolve_timezone(db)
    if not date_from and not date_to:
        today = business_day_service.shop_today(tz)
        start = datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(
            timezone.utc
        )
        now = utcnow()
        return (
            today,
            None,
            str(tz),
            start,
            now,
            start - timedelta(days=1),
            now - timedelta(days=1),
        )

    if not (date_from and date_to):
        raise BadRequestError("Provide both date_from and date_to, or neither")
    try:
        d_from = date.fromisoformat(date_from)
        d_to = date.fromisoformat(date_to)
    except ValueError as exc:
        raise BadRequestError("Dates must be ISO 8601 (YYYY-MM-DD)") from exc
    if d_from > d_to:
        raise BadRequestError("date_from must not be after date_to")

    start = datetime(d_from.year, d_from.month, d_from.day, tzinfo=tz).astimezone(
        timezone.utc
    )
    # End = the last instant of `to`'s local day (start of the day after, minus a
    # microsecond) so the inclusive `created_at <= end` filters own the whole day.
    end_local = datetime(d_to.year, d_to.month, d_to.day, tzinfo=tz) + timedelta(days=1)
    end = end_local.astimezone(timezone.utc) - timedelta(microseconds=1)
    span = timedelta(days=(d_to - d_from).days + 1)
    return d_from, d_to.isoformat(), str(tz), start, end, start - span, end - span


@router.get("/today", response_model=DashboardTodayResponse)
async def dashboard_today(
    date_from: str | None = Query(None, description="ISO date; with date_to, a range"),
    date_to: str | None = Query(None, description="ISO date; with date_from, a range"),
    statuses: list[str] | None = Query(
        None, description="Narrow every figure to these order statuses (multi-select)"
    ),
    couriers: list[str] | None = Query(
        None,
        description="Narrow every figure to these carriers (multi-select) — "
        "`counter`, an aggregator marketplace, or a dispatch courier code",
    ),
    branch_ids: list[uuid.UUID] | None = Query(
        None, description="Narrow every figure to these fulfilling branches (multi)"
    ),
    legal_entity_ids: list[uuid.UUID] | None = Query(
        None, description="Narrow every figure to these legal entities (multi)"
    ),
    category_ids: list[uuid.UUID] | None = Query(
        None,
        description="Narrow every figure to orders holding a line in these "
        "product categories (multi)",
    ),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("dashboard.access")),
):
    """The trading day — or any date range — at a glance, over every channel.

    No dates → the live current day (unchanged). A `date_from`/`date_to` pair →
    that range. An optional `statuses` selection narrows the headline figures and
    the channel/fulfilment/payment mix to those statuses; `by_status` always
    reports the full spread so it can drive the selector.
    """
    (
        from_date,
        to_date,
        tz_name,
        start,
        end,
        prior_start,
        prior_end,
    ) = await _range_bounds(db, date_from, date_to)
    picked = statuses or None
    carriers = couriers or None
    branches = branch_ids or None
    entities = legal_entity_ids or None
    cats = category_ids or None
    # Branch, legal-entity + category where-clauses, reused by the figures that
    # build their own query (delivered, by_status) rather than going through
    # `_filters`.
    dim_clauses = [
        c
        for c in (
            _branch_clause(branches),
            _legal_entity_clause(entities),
            order_query.category_clause(cats),
        )
        if c is not None
    ]

    orders_cur, revenue_cur = await _window_totals(
        db,
        start=start,
        end=end,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )
    orders_prev, revenue_prev = await _window_totals(
        db,
        start=prior_start,
        end=prior_end,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    delivered_clause = order_query.courier_clause(carriers)
    delivered = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.created_at >= start,
            Order.created_at <= end,
            order_query.fulfilled_clause(),
            *([delivered_clause] if delivered_clause is not None else []),
            *dim_clauses,
        ),
    )

    summary = DashboardSummary(
        orders=orders_cur,
        revenue=revenue_cur,
        avg_order_value=round(revenue_cur / orders_cur, 2) if orders_cur else 0.0,
        delivered=delivered,
        orders_growth=_growth(orders_cur, orders_prev),
        revenue_growth=_growth(revenue_cur, revenue_prev),
    )

    # by_status keeps the FULL status spread (cancellations included) regardless
    # of the status selection — it is the menu the operator picks that selection
    # from — but it does follow the carrier, branch and legal-entity filters, so
    # picking any of those narrows the status menu the same way.
    courier_only = order_query.courier_clause(carriers)
    status_rows = (
        await db.execute(
            select(
                Order.status,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(
                Order.created_at >= start,
                Order.created_at <= end,
                *([courier_only] if courier_only is not None else []),
                *dim_clauses,
            )
            .group_by(Order.status)
            .order_by(func.count(Order.id).desc())
        )
    ).all()
    by_status = [
        BreakdownRow(
            label=str(getattr(s, "value", s)).replace("_", " ").title(),
            orders=int(c),
            revenue=float(money(r)),
        )
        for s, c, r in status_rows
    ]

    by_courier = await _by_courier(
        db,
        start=start,
        end=end,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    # The branch selector: full branch spread, following every other dimension.
    by_branch = await _by_branch(
        db,
        start=start,
        end=end,
        statuses=picked,
        couriers=carriers,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    # The legal-entity selector: full entity spread, following every other dimension.
    by_legal_entity = await _by_legal_entity(
        db,
        start=start,
        end=end,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        category_ids=cats,
    )

    # The category selector: full category spread, following every other dimension.
    by_category = await _by_category(
        db,
        start=start,
        end=end,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
    )

    by_channel = await _breakdown(
        db,
        _CHANNEL_EXPR,
        start=start,
        end=end,
        labels=_CHANNEL_LABELS,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )
    by_fulfillment = await _breakdown(
        db,
        Order.delivery_method,
        start=start,
        end=end,
        labels={
            DeliveryMethodEnum.DELIVERY.value: "Delivery",
            DeliveryMethodEnum.PICKUP.value: "Pickup",
        },
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )
    by_payment = await _payment_breakdown(
        db,
        start=start,
        end=end,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    # Hourly for a single day (the live day or a from==to range), daily otherwise.
    granularity = "day" if (to_date and to_date != from_date.isoformat()) else "hour"
    series = await _series(
        db,
        start=start,
        end=end,
        tz_name=tz_name,
        granularity=granularity,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    heatmap = await _heatmap(
        db,
        start=start,
        end=end,
        tz_name=tz_name,
        statuses=picked,
        couriers=carriers,
        branch_ids=branches,
        legal_entity_ids=entities,
        category_ids=cats,
    )

    ops = await _operational_snapshot(db, start=start, end=end, today=from_date)

    return DashboardTodayResponse(
        business_date=from_date.isoformat(),
        business_date_to=to_date,
        timezone=tz_name,
        generated_at=utcnow(),
        summary=summary,
        by_status=by_status,
        by_courier=by_courier,
        by_branch=by_branch,
        by_legal_entity=by_legal_entity,
        by_category=by_category,
        by_channel=by_channel,
        by_fulfillment=by_fulfillment,
        by_payment=by_payment,
        series=series,
        series_granularity=granularity,
        heatmap=heatmap,
        ops=ops,
    )


async def _operational_snapshot(
    db: AsyncSession, *, start: datetime, end: datetime, today: date
) -> DashboardOps:
    """The open work an admin acts on now — mostly current-state, some today-only."""
    out_for_delivery = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.status == OrderStatusEnum.OUT_FOR_DELIVERY
        ),
    )
    undelivered = await _count(
        db,
        select(func.count(Order.id)).where(Order.status == OrderStatusEnum.UNDELIVERED),
    )
    payment_failed_today = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.created_at >= start,
            Order.created_at <= end,
            Order.status == OrderStatusEnum.PAYMENT_FAILED,
        ),
    )

    refunds = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.refunded_amount), 0),
            ).where(
                Order.refunded_at.is_not(None),
                Order.refunded_at >= start,
                Order.refunded_at <= end,
            )
        )
    ).one()

    open_custom = await _count(
        db,
        select(func.count(CustomOrder.id)).where(
            CustomOrder.status.in_(
                (
                    CustomOrderStatusEnum.ENQUIRY.value,
                    CustomOrderStatusEnum.CONFIRMED.value,
                    CustomOrderStatusEnum.IN_PRODUCTION.value,
                    CustomOrderStatusEnum.READY.value,
                )
            )
        ),
    )
    custom_due_today = await _count(
        db,
        select(func.count(CustomOrder.id)).where(
            CustomOrder.due_date == today,
            CustomOrder.status.not_in(
                (
                    CustomOrderStatusEnum.COMPLETED.value,
                    CustomOrderStatusEnum.CANCELLED.value,
                )
            ),
        ),
    )

    low_stock = await _count(
        db,
        select(func.count())
        .select_from(InventoryLevel)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .where(
            InventoryItem.deleted_at.is_(None),
            InventoryItem.is_active.is_(True),
            InventoryLevel.quantity < InventoryItem.minimum_level,
        ),
    )
    pending_pos = await _count(
        db,
        select(func.count(PurchaseOrder.id)).where(
            PurchaseOrder.status == PurchaseOrderStatusEnum.PENDING.value
        ),
    )
    open_tills = await _count(
        db,
        select(func.count(Till.id)).where(Till.status == TillStatusEnum.OPEN.value),
    )
    active_couriers = await _count(
        db,
        select(func.count(Courier.id)).where(Courier.is_active.is_(True)),
    )

    return DashboardOps(
        out_for_delivery=out_for_delivery,
        undelivered=undelivered,
        payment_failed_today=payment_failed_today,
        refunds_today=int(refunds[0]),
        refunds_amount_today=float(money(refunds[1])),
        open_custom_orders=open_custom,
        custom_orders_due_today=custom_due_today,
        low_stock_items=low_stock,
        pending_purchase_orders=pending_pos,
        open_tills=open_tills,
        active_couriers=active_couriers,
    )
