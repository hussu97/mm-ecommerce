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

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
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
from app.schemas.dashboard import (
    BreakdownRow,
    DashboardOps,
    DashboardSummary,
    DashboardTodayResponse,
)
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


async def _day_bounds(db: AsyncSession) -> tuple[date, str, datetime, datetime]:
    """
    The current shop day as `(today, tz_name, start_utc, now_utc)`.

    `start_utc` is local midnight where the shop is, expressed as the UTC instant
    it actually happened at, so a `created_at >= start_utc` filter is exact at
    the day boundary.
    """
    tz = await business_day_service.resolve_timezone(db)
    today = business_day_service.shop_today(tz)
    start_local = datetime(today.year, today.month, today.day, tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    return today, str(tz), start_utc, utcnow()


async def _count(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one())


async def _breakdown(
    db: AsyncSession, column, *, start, end, labels=None
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
                _REVENUE_STATUSES,
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


async def _window_totals(db: AsyncSession, *, start, end) -> tuple[int, float]:
    """(orders, revenue) for revenue-eligible orders created in the window."""
    result = (
        await db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            ).where(
                Order.created_at >= start,
                Order.created_at <= end,
                _REVENUE_STATUSES,
            )
        )
    ).one()
    return int(result[0]), float(money(result[1]))


def _growth(current: float, prior: float) -> float:
    """Percentage change, guarding the "off nothing" case that has no rate."""
    if prior <= 0:
        return 0.0
    return round((current - prior) / prior * 100, 1)


@router.get("/today", response_model=DashboardTodayResponse)
async def dashboard_today(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("dashboard.access")),
):
    """The current trading day at a glance, over every order and every channel."""
    today, tz_name, start, now = await _day_bounds(db)
    business_date = today.isoformat()

    # Headline figures, and the same elapsed window yesterday for a growth rate.
    orders_today, revenue_today = await _window_totals(db, start=start, end=now)
    y_start = start - timedelta(days=1)
    y_end = now - timedelta(days=1)
    orders_prev, revenue_prev = await _window_totals(db, start=y_start, end=y_end)

    delivered = await _count(
        db,
        select(func.count(Order.id)).where(
            Order.created_at >= start,
            Order.created_at <= now,
            Order.status == OrderStatusEnum.DELIVERED,
        ),
    )

    summary = DashboardSummary(
        orders=orders_today,
        revenue=revenue_today,
        avg_order_value=round(revenue_today / orders_today, 2) if orders_today else 0.0,
        delivered=delivered,
        orders_growth=_growth(orders_today, orders_prev),
        revenue_growth=_growth(revenue_today, revenue_prev),
    )

    # by_status is the one breakdown that keeps cancellations in view.
    status_rows = (
        await db.execute(
            select(
                Order.status,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total), 0),
            )
            .where(Order.created_at >= start, Order.created_at <= now)
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

    by_channel = await _breakdown(
        db, Order.source, start=start, end=now, labels=_CHANNEL_LABELS
    )
    by_fulfillment = await _breakdown(
        db,
        Order.delivery_method,
        start=start,
        end=now,
        labels={
            DeliveryMethodEnum.DELIVERY.value: "Delivery",
            DeliveryMethodEnum.PICKUP.value: "Pickup",
        },
    )
    by_payment = await _breakdown(
        db,
        Order.payment_method,
        start=start,
        end=now,
        labels={"card": "Card", "cod": "Cash on delivery"},
    )

    ops = await _operational_snapshot(db, start=start, end=now, today=today)

    return DashboardTodayResponse(
        business_date=business_date,
        timezone=tz_name,
        generated_at=now,
        summary=summary,
        by_status=by_status,
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
