"""
The console's commerce figures, read from our own tables.

Everything here answers a question about the past off `orders`, and is cached
for `_ANALYTICS_TTL` because the past does not move. Traffic and funnel live in
`umami.py` because they come from somebody else's API; live baskets live in
`live_carts.py` because they are the one thing on this screen that must not be
cached at all.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.deps import get_db
from app.core.permissions import require
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.user import User

from ._shared import _ANALYTICS_TTL, _date_range
from .schemas import (
    BreakdownItem,
    CustomerBreakdown,
    OrdersPoint,
    OverviewResponse,
    PromoPerformance,
    RevenueBreakdown,
    RevenuePoint,
    TopProduct,
    ZoneData,
)

router = APIRouter()


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("dashboard.access")),
):
    """Revenue, orders, customers, and growth vs prior period."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:overview:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return OverviewResponse(**cached)

    stmt = select(
        func.coalesce(func.sum(Order.total), 0).label("revenue"),
        func.count(Order.id).label("orders"),
        func.count(func.distinct(Order.user_id)).label("customers"),
    ).where(
        Order.status != OrderStatusEnum.CANCELLED,
        func.date(Order.created_at) >= start,
        func.date(Order.created_at) <= end,
    )
    result = (await db.execute(stmt)).one()

    total_revenue = float(result.revenue)
    total_orders = int(result.orders)
    total_customers = int(result.customers)
    avg = total_revenue / total_orders if total_orders else 0.0

    # Prior period for growth comparison
    period_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)

    prev_stmt = select(
        func.coalesce(func.sum(Order.total), 0).label("revenue"),
        func.count(Order.id).label("orders"),
    ).where(
        Order.status != OrderStatusEnum.CANCELLED,
        func.date(Order.created_at) >= prev_start,
        func.date(Order.created_at) <= prev_end,
    )
    prev = (await db.execute(prev_stmt)).one()
    prev_rev = float(prev.revenue)
    prev_orders = int(prev.orders)

    rev_growth = ((total_revenue - prev_rev) / prev_rev * 100) if prev_rev else 0.0
    orders_growth = (
        ((total_orders - prev_orders) / prev_orders * 100) if prev_orders else 0.0
    )

    result_obj = OverviewResponse(
        total_revenue=total_revenue,
        total_orders=total_orders,
        avg_order_value=round(avg, 2),
        total_customers=total_customers,
        revenue_growth=round(rev_growth, 1),
        orders_growth=round(orders_growth, 1),
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


@router.get("/revenue", response_model=list[RevenuePoint])
async def get_revenue(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Daily/weekly/monthly revenue totals."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:revenue:{start}:{end}:{group_by}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [RevenuePoint(**item) for item in cached]

    trunc = func.date_trunc(group_by, Order.created_at)

    stmt = (
        select(
            trunc.label("period"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by("period")
        .order_by("period")
    )
    rows = (await db.execute(stmt)).all()

    result_list = [
        RevenuePoint(
            date=row.period.strftime("%Y-%m-%d") if row.period else "",
            revenue=float(row.revenue),
        )
        for row in rows
    ]
    await cache_set(
        cache_key,
        [item.model_dump(mode="json") for item in result_list],
        ttl=_ANALYTICS_TTL,
    )
    return result_list


@router.get("/orders-chart", response_model=list[OrdersPoint])
async def get_orders_chart(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    group_by: str = Query("day", pattern="^(day|week|month)$"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Daily/weekly/monthly order counts."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:orders-chart:{start}:{end}:{group_by}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [OrdersPoint(**item) for item in cached]

    trunc = func.date_trunc(group_by, Order.created_at)

    stmt = (
        select(trunc.label("period"), func.count(Order.id).label("count"))
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by("period")
        .order_by("period")
    )
    rows = (await db.execute(stmt)).all()

    result_list = [
        OrdersPoint(
            date=row.period.strftime("%Y-%m-%d") if row.period else "",
            count=int(row.count),
        )
        for row in rows
    ]
    await cache_set(
        cache_key,
        [item.model_dump(mode="json") for item in result_list],
        ttl=_ANALYTICS_TTL,
    )
    return result_list


@router.get("/top-products", response_model=list[TopProduct])
async def get_top_products(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Top products by revenue."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:top-products:{start}:{end}:{limit}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [TopProduct(**item) for item in cached]

    stmt = (
        select(
            OrderItem.product_name,
            OrderItem.product_sku,
            func.coalesce(func.sum(OrderItem.total_price), 0).label("revenue"),
            func.coalesce(func.sum(OrderItem.quantity), 0).label("quantity"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(OrderItem.product_name, OrderItem.product_sku)
        .order_by(func.sum(OrderItem.total_price).desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    result_list = [
        TopProduct(
            product_name=row.product_name,
            product_sku=row.product_sku,
            revenue=float(row.revenue),
            quantity=int(row.quantity),
        )
        for row in rows
    ]
    await cache_set(
        cache_key,
        [item.model_dump(mode="json") for item in result_list],
        ttl=_ANALYTICS_TTL,
    )
    return result_list


@router.get("/customers", response_model=CustomerBreakdown)
async def get_customers(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Customer type breakdown: registered vs guest, new vs returning."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:customers:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return CustomerBreakdown(**cached)

    # Registered vs guest — orders in range joined to users
    reg_stmt = (
        select(
            func.count(Order.id).filter(User.is_guest == False).label("registered"),  # noqa: E712
            func.count(Order.id)
            .filter(
                (User.is_guest == True) | (Order.user_id == None)  # noqa: E711,E712
            )
            .label("guest"),
        )
        .select_from(Order)
        .outerjoin(User, User.id == Order.user_id)
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
    )
    reg_result = (await db.execute(reg_stmt)).one()
    registered = int(reg_result.registered)
    guest = int(reg_result.guest)

    # New customers: users whose FIRST ever order falls in the date range
    first_order_sub = (
        select(Order.user_id, func.min(Order.created_at).label("first_at"))
        .where(Order.user_id != None)  # noqa: E711
        .group_by(Order.user_id)
        .subquery()
    )
    new_stmt = (
        select(func.count())
        .select_from(first_order_sub)
        .where(
            func.date(first_order_sub.c.first_at) >= start,
            func.date(first_order_sub.c.first_at) <= end,
        )
    )
    new_customers = int((await db.execute(new_stmt)).scalar() or 0)

    # Returning: users with ≥1 order before start AND ≥1 order in range
    before_sub = (
        select(func.distinct(Order.user_id).label("uid"))
        .where(Order.user_id != None, func.date(Order.created_at) < start)  # noqa: E711
        .subquery()
    )
    in_range_sub = (
        select(func.distinct(Order.user_id).label("uid"))
        .where(
            Order.user_id != None,  # noqa: E711
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .subquery()
    )
    returning_stmt = (
        select(func.count())
        .select_from(in_range_sub)
        .where(in_range_sub.c.uid.in_(select(before_sub.c.uid)))
    )
    returning_customers = int((await db.execute(returning_stmt)).scalar() or 0)

    result_obj = CustomerBreakdown(
        registered=registered,
        guest=guest,
        new_customers=new_customers,
        returning_customers=returning_customers,
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


#: Values of `payment_provider` that are not a card processor.
#:
#: Cash sets the column to `cod`, which was reasonable when the column was the
#: only place either question could be answered. It is excluded from the gateway
#: chart rather than renamed, because rewriting it on tens of thousands of live
#: rows to tidy up one chart is not a trade worth making.
_NOT_A_GATEWAY = ("cod", "none")


def _payment_method_label(value: str | None) -> str:
    """
    A stored `payment_method`, as one of the two things it can mean.

    Mirrors `payment_methods.normalise_method` deliberately loosely: that one
    raises on a word it does not know, which is right at the edge of the system
    and wrong in a chart. An unrecognised value here is shown as itself, because
    a breakdown that hides a row it did not expect is a breakdown whose total
    quietly stops adding up.
    """
    if not value:
        return "unknown"
    lowered = value.strip().lower()
    if lowered == "cod":
        return "cod"
    if lowered in ("card", "stripe", "ziina"):
        return "card"
    return lowered


def _merged(rows) -> list[BreakdownItem]:
    """
    Sum rows that share a label, biggest first.

    Needed because normalising `stripe` and `card` to the same slice means two
    GROUP BY rows land on one label, and a chart drawn from the un-merged list
    shows "card" twice with the split falling wherever the deploy happened to
    land.
    """
    totals: dict[str, list[float]] = {}
    for label, orders, revenue in rows:
        bucket = totals.setdefault(label, [0, 0.0])
        bucket[0] += orders
        bucket[1] += revenue
    return [
        BreakdownItem(label=label, orders=int(orders), revenue=revenue)
        for label, (orders, revenue) in sorted(
            totals.items(), key=lambda kv: kv[1][1], reverse=True
        )
    ]


@router.get("/revenue-breakdown", response_model=RevenueBreakdown)
async def get_revenue_breakdown(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """
    Revenue split by delivery method, by payment method, and by gateway.

    The last two used to be one chart, which worked only for as long as there
    was exactly one card processor: `{stripe, cod}` read naturally as "how did
    they pay". With two it reads as neither — `{stripe, ziina, cod}` puts a
    commercial question and an operational one on the same axis and answers
    both badly. So: *method* is the commercial split and is what the business
    cares about; *gateway* is card-only and is what you look at during a
    processor incident.
    """
    start, end = _date_range(start_date, end_date)

    # `v2` because the shape changed. A key left alone would read back an entry
    # written by the previous release, find no `by_payment_method` in it, and
    # fail validation — turning a deploy into a broken analytics page for
    # however long the TTL had left to run.
    cache_key = f"analytics:revenue-breakdown:v2:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return RevenueBreakdown(**cached)

    base_filter = [
        Order.status != OrderStatusEnum.CANCELLED,
        func.date(Order.created_at) >= start,
        func.date(Order.created_at) <= end,
    ]

    delivery_stmt = (
        select(
            Order.delivery_method.label("label"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .where(*base_filter)
        .group_by(Order.delivery_method)
        .order_by(func.sum(Order.total).desc())
    )
    delivery_rows = (await db.execute(delivery_stmt)).all()

    # How they chose to pay. Read off `payment_method`, and normalised, because
    # every card order written before methods and gateways were split holds
    # `stripe` in that column — leaving it raw would show a permanent phantom
    # third slice that shrinks as old orders age out of the window.
    method_stmt = (
        select(
            Order.payment_method.label("label"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .where(*base_filter)
        .group_by(Order.payment_method)
    )
    method_rows = (await db.execute(method_stmt)).all()
    by_method = _merged(
        (_payment_method_label(r.label), int(r.orders), float(r.revenue))
        for r in method_rows
    )

    # Who settled the card orders. Cash is excluded: it has no gateway, and a
    # `cod` slice in a chart about processors is the same conflation this split
    # was made to remove.
    gateway_stmt = (
        select(
            Order.payment_provider.label("label"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .where(*base_filter, Order.payment_provider.notin_(_NOT_A_GATEWAY))
        .group_by(Order.payment_provider)
        .order_by(func.sum(Order.total).desc())
    )
    gateway_rows = (await db.execute(gateway_stmt)).all()

    result_obj = RevenueBreakdown(
        by_delivery_method=[
            BreakdownItem(
                label=str(r.label), orders=int(r.orders), revenue=float(r.revenue)
            )
            for r in delivery_rows
        ],
        by_payment_method=by_method,
        by_payment_gateway=[
            BreakdownItem(
                label=str(r.label) if r.label else "unknown",
                orders=int(r.orders),
                revenue=float(r.revenue),
            )
            for r in gateway_rows
        ],
        # Same rows as `by_payment_method`, under the old name. See the schema.
        by_payment_provider=by_method,
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


@router.get("/zones", response_model=list[ZoneData])
async def get_zones(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """
    Sales by delivery zone.

    Grouped by the zone that actually priced each order, snapshotted on its
    delivery record. It used to group by the emirate the customer picked from
    a dropdown, which made this a report on what people typed rather than on
    where the cakes went.
    """
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:zones:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [ZoneData(**item) for item in cached]

    stmt = (
        select(
            OrderDelivery.zone_name.label("zone"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .join(OrderDelivery, OrderDelivery.order_id == Order.id)
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            OrderDelivery.zone_name.isnot(None),
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(OrderDelivery.zone_name)
        .order_by(func.sum(Order.total).desc())
    )
    rows = (await db.execute(stmt)).all()

    result_list = [
        ZoneData(zone=str(r.zone), orders=int(r.orders), revenue=float(r.revenue))
        for r in rows
        if r.zone
    ]
    await cache_set(
        cache_key,
        [item.model_dump(mode="json") for item in result_list],
        ttl=_ANALYTICS_TTL,
    )
    return result_list


@router.get("/promos", response_model=list[PromoPerformance])
async def get_promos(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
):
    """Promo code performance: uses, revenue driven, discount given."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:promos:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [PromoPerformance(**item) for item in cached]

    stmt = (
        select(
            Order.promo_code_used.label("code"),
            func.count(Order.id).label("uses"),
            func.coalesce(func.sum(Order.total), 0).label("revenue_driven"),
            func.coalesce(func.sum(Order.discount_amount), 0).label("discount_given"),
        )
        .where(
            Order.status != OrderStatusEnum.CANCELLED,
            Order.promo_code_used != None,  # noqa: E711
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(Order.promo_code_used)
        .order_by(func.count(Order.id).desc())
    )
    rows = (await db.execute(stmt)).all()

    result_list = [
        PromoPerformance(
            code=str(r.code),
            uses=int(r.uses),
            revenue_driven=float(r.revenue_driven),
            discount_given=float(r.discount_given),
        )
        for r in rows
    ]
    await cache_set(
        cache_key,
        [item.model_dump(mode="json") for item in result_list],
        ttl=_ANALYTICS_TTL,
    )
    return result_list
