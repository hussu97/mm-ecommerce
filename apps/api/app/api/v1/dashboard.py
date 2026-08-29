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

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.money import money
from app.core.permissions import require
from app.models import (
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
from app.models.order_delivery import OrderDelivery
from app.schemas.dashboard import (
    BreakdownRow,
    CourierBreakdownRow,
    DashboardOps,
    DashboardSummary,
    DashboardTodayResponse,
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
_CHANNEL_LABELS = {
    OrderSourceEnum.ONLINE.value: "Storefront",
    OrderSourceEnum.CASHIER.value: "Counter",
    OrderSourceEnum.AGGREGATOR.value: "Aggregator",
    OrderSourceEnum.CALL_CENTER.value: "Call centre",
    OrderSourceEnum.API.value: "API",
}


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


def _filters(statuses, couriers):
    """The status and courier where-clauses shared by every windowed figure."""
    clauses = [_status_clause(statuses)]
    courier_clause = order_query.courier_clause(couriers)
    if courier_clause is not None:
        clauses.append(courier_clause)
    return clauses


async def _breakdown(
    db: AsyncSession, column, *, start, end, labels=None, statuses=None, couriers=None
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
                *_filters(statuses, couriers),
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
            BreakdownRow(label=label, orders=int(count), revenue=float(money(revenue)))
        )
    return out


async def _window_totals(
    db: AsyncSession, *, start, end, statuses=None, couriers=None
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
                *_filters(statuses, couriers),
            )
        )
    ).one()
    return int(result[0]), float(money(result[1]))


async def _by_courier(db: AsyncSession, *, start, end) -> list[CourierBreakdownRow]:
    """Delivered orders and revenue per carrier over the window.

    Grouped in Python via `order_query.courier_code_for` rather than in SQL,
    because "which courier" spans three different columns (source for the
    counter, `aggregator_channel` for a marketplace, the delivery record's
    provider for a dispatch courier) and there is no single column to group by.
    Delivered-only, so this reads as settled courier revenue. One row per known
    courier code, busiest first; a delivered order with no resolvable carrier
    (an online pickup) counts under none.
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
                provider.label("provider"),
            ).where(
                Order.created_at >= start,
                Order.created_at <= end,
                Order.status == OrderStatusEnum.DELIVERED,
            )
        )
    ).all()

    totals: dict[str, list] = {code: [0, 0.0] for code in order_query.ALL_COURIER_CODES}
    for source, channel, total, prov in rows:
        code = order_query.courier_code_for(
            getattr(source, "value", source), channel, prov
        )
        if code is None or code not in totals:
            continue
        totals[code][0] += 1
        totals[code][1] += float(total or 0)

    out = [
        CourierBreakdownRow(
            code=code,
            label=order_query.courier_label(code),
            logo_url=(
                None
                if code == order_query.COUNTER_CODE
                else courier_catalog.logo_url_for(code)
            ),
            orders=orders,
            revenue=float(money(revenue)),
        )
        for code, (orders, revenue) in totals.items()
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

    orders_cur, revenue_cur = await _window_totals(
        db, start=start, end=end, statuses=picked, couriers=carriers
    )
    orders_prev, revenue_prev = await _window_totals(
        db, start=prior_start, end=prior_end, statuses=picked, couriers=carriers
    )

    delivered_clause = order_query.courier_clause(carriers)
    delivered = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.created_at >= start,
            Order.created_at <= end,
            Order.status == OrderStatusEnum.DELIVERED,
            *([delivered_clause] if delivered_clause is not None else []),
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
    # from — but it does follow the courier filter, so picking a carrier narrows
    # the status menu to that carrier.
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

    by_courier = await _by_courier(db, start=start, end=end)

    by_channel = await _breakdown(
        db,
        Order.source,
        start=start,
        end=end,
        labels=_CHANNEL_LABELS,
        statuses=picked,
        couriers=carriers,
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
    )
    by_payment = await _breakdown(
        db,
        Order.payment_method,
        start=start,
        end=end,
        labels={"card": "Card", "cod": "Cash on delivery"},
        statuses=picked,
        couriers=carriers,
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
        by_channel=by_channel,
        by_fulfillment=by_fulfillment,
        by_payment=by_payment,
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
