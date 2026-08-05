from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set, cache_delete_pattern  # noqa: F401
from app.core.config import settings
from app.core.deps import get_admin_user, get_db
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.user import User

logger = logging.getLogger(__name__)

_ANALYTICS_TTL = 300

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _date_range(
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[date, date]:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)
    return start_date, end_date


def _to_ms(d: date) -> int:
    """Convert date to milliseconds timestamp (Umami API format)."""
    from datetime import datetime

    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


# ─── Response schemas ─────────────────────────────────────────────────────────


class OverviewResponse(BaseModel):
    total_revenue: float
    total_orders: int
    avg_order_value: float
    total_customers: int
    revenue_growth: float
    orders_growth: float


class RevenuePoint(BaseModel):
    date: str
    revenue: float


class OrdersPoint(BaseModel):
    date: str
    count: int


class TopProduct(BaseModel):
    product_name: str
    product_sku: str
    revenue: float
    quantity: int


class FunnelData(BaseModel):
    created: int
    confirmed: int
    packed: int
    cancelled: int
    conversion_rate: float


# New schemas


class PageviewPoint(BaseModel):
    date: str
    views: int


class TopPage(BaseModel):
    path: str
    views: int


class EventCount(BaseModel):
    """One custom event from `apps/web/lib/analytics.ts`, and how often it fired."""

    name: str
    count: int


class TrafficData(BaseModel):
    visitors: int
    sessions: int
    pageviews: int
    bounce_rate: float
    avg_duration: float
    pageviews_chart: list[PageviewPoint]
    top_pages: list[TopPage]
    #: Custom events, commonest first. Empty is a real answer — it means the
    #: storefront tracked nothing in this window, which is worth seeing.
    events: list[EventCount]
    configured: bool
    #: Why the numbers above are zero, when the reason is us and not the shop.
    #: `None` when Umami answered. Anything else is a configuration problem the
    #: owner can act on, and used to be indistinguishable from a quiet week.
    error: str | None = None


class CustomerBreakdown(BaseModel):
    registered: int
    guest: int
    new_customers: int
    returning_customers: int


class BreakdownItem(BaseModel):
    label: str
    orders: int
    revenue: float


class RevenueBreakdown(BaseModel):
    by_delivery_method: list[BreakdownItem]
    by_payment_provider: list[BreakdownItem]


class ZoneData(BaseModel):
    zone: str
    orders: int
    revenue: float


class PromoPerformance(BaseModel):
    code: str
    uses: int
    revenue_driven: float
    discount_given: float


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=OverviewResponse)
async def get_overview(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
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
    _admin: User = Depends(get_admin_user),
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
    _admin: User = Depends(get_admin_user),
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
    _admin: User = Depends(get_admin_user),
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


@router.get("/funnel", response_model=FunnelData)
async def get_funnel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Order counts by status (funnel)."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:funnel:{start}:{end}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return FunnelData(**cached)

    stmt = (
        select(Order.status, func.count(Order.id).label("count"))
        .where(
            func.date(Order.created_at) >= start,
            func.date(Order.created_at) <= end,
        )
        .group_by(Order.status)
    )
    rows = (await db.execute(stmt)).all()
    counts: dict[OrderStatusEnum, int] = {row.status: int(row.count) for row in rows}

    created = counts.get(OrderStatusEnum.CREATED, 0)
    confirmed = counts.get(OrderStatusEnum.CONFIRMED, 0)
    packed = counts.get(OrderStatusEnum.PACKED, 0)
    cancelled = counts.get(OrderStatusEnum.CANCELLED, 0)
    total = created + confirmed + packed + cancelled

    conversion_rate = round(packed / total * 100, 1) if total else 0.0

    result_obj = FunnelData(
        created=created,
        confirmed=confirmed,
        packed=packed,
        cancelled=cancelled,
        conversion_rate=conversion_rate,
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


@router.get("/traffic", response_model=TrafficData)
async def get_traffic(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    _admin: User = Depends(get_admin_user),
):
    """
    Traffic and custom events, read back from Umami Cloud.

    Every failure here used to return zeros. A revoked key, a plan whose API
    access has lapsed, a website ID pointing at a site nobody visits and a genuinely
    quiet Tuesday all rendered as the same four noughts on the dashboard, and the
    only way to tell them apart was to read the API's reply by hand from the VM.
    They are told apart now: `error` carries the reason when the reason is ours.
    """

    def _empty(*, configured: bool, error: str | None = None) -> TrafficData:
        return TrafficData(
            visitors=0,
            sessions=0,
            pageviews=0,
            bounce_rate=0.0,
            avg_duration=0.0,
            pageviews_chart=[],
            top_pages=[],
            events=[],
            configured=configured,
            error=error,
        )

    if not settings.UMAMI_API_KEY or not settings.UMAMI_WEBSITE_ID:
        return _empty(configured=False)

    start, end = _date_range(start_date, end_date)
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) + 86_399_999  # inclusive end-of-day

    base = f"https://api.umami.is/v1/websites/{settings.UMAMI_WEBSITE_ID}"
    headers = {"x-umami-api-key": settings.UMAMI_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            responses = await _umami_fetch_all(client, base, headers, start_ms, end_ms)
    except Exception as exc:
        logger.warning("Umami read failed: %s", exc)
        return _empty(configured=True, error=f"Could not reach Umami: {exc}")

    failure = _umami_failure(responses)
    if failure:
        logger.warning("Umami read rejected: %s", failure)
        return _empty(configured=True, error=failure)

    stats_resp, pv_resp, pages_resp, events_resp = (
        _umami_json(responses[0], {}),
        _umami_json(responses[1], {}),
        _umami_json(responses[2], []),
        _umami_json(responses[3], []),
    )

    try:
        # Umami v2 returns flat integers: {"visitors": 1, "pageviews": 5, ...}
        # with an optional nested "comparison" key
        def _stat(key: str) -> float:
            v = stats_resp.get(key, 0)
            # older Umami versions returned {"value": N}
            if isinstance(v, dict):
                return float(v.get("value", 0))
            return float(v)

        visitors = int(_stat("visitors"))
        sessions = int(_stat("visits"))  # Umami uses "visits" not "sessions"
        pageviews = int(_stat("pageviews"))

        # Umami reports `bounces` as a count of single-page visits and
        # `totaltime` as a sum of seconds. The dashboard prints one as a
        # percentage and the other as an average, so the division belongs here
        # — without it a good week read as a 300% bounce rate.
        bounce_rate = (_stat("bounces") / sessions * 100) if sessions else 0.0
        avg_duration = (_stat("totaltime") / sessions) if sessions else 0.0

        chart_items = pv_resp.get("pageviews", [])
        chart = [
            PageviewPoint(date=item.get("x", ""), views=int(item.get("y", 0)))
            for item in chart_items
        ]

        top = [
            TopPage(path=item.get("x", ""), views=int(item.get("y", 0)))
            for item in pages_resp[:10]
        ]

        events = sorted(
            (
                EventCount(name=item.get("x", ""), count=int(item.get("y", 0)))
                for item in events_resp
                if item.get("x")
            ),
            key=lambda e: e.count,
            reverse=True,
        )

        return TrafficData(
            visitors=visitors,
            sessions=sessions,
            pageviews=pageviews,
            bounce_rate=round(bounce_rate, 1),
            avg_duration=round(avg_duration, 0),
            pageviews_chart=chart,
            top_pages=top,
            events=events,
            configured=True,
        )
    except Exception as exc:
        logger.warning("Umami reply was not the shape we expected: %s", exc)
        return _empty(configured=True, error=f"Unreadable reply from Umami: {exc}")


async def _umami_fetch_all(
    client: httpx.AsyncClient, base: str, headers: dict, start_ms: int, end_ms: int
) -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
    """
    Stats, the pageview series, top paths, and the custom events.

    The responses come back whole rather than pre-parsed, because whether Umami
    said yes is as much of the answer as what it said.
    """
    import asyncio

    params = {"startAt": start_ms, "endAt": end_ms}

    async def fetch(url: str, extra: dict | None = None) -> httpx.Response:
        p = {**params, **(extra or {})}
        return await client.get(url, headers=headers, params=p)

    return await asyncio.gather(  # type: ignore[return-value]
        fetch(f"{base}/stats"),
        fetch(f"{base}/pageviews", {"unit": "day", "timezone": "Asia/Dubai"}),
        fetch(f"{base}/metrics", {"type": "path"}),
        # Every name `apps/web/lib/analytics.ts` tracks — add_to_cart,
        # begin_checkout, order_completed and the rest. Nothing read these
        # before, so the dashboard could not have shown a checkout event
        # arriving even when one did.
        fetch(f"{base}/metrics", {"type": "event"}),
    )


def _umami_failure(responses: tuple[httpx.Response, ...]) -> str | None:
    """The first refusal in a batch, phrased for somebody who has to fix it."""
    for resp in responses:
        if resp.is_success:
            continue
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error")
                detail = (
                    err.get("message", "") if isinstance(err, dict) else str(err or "")
                )
        except Exception:
            detail = resp.text[:120]
        if resp.status_code in (401, 403):
            return (
                f"Umami refused the API key ({resp.status_code}"
                f"{': ' + detail if detail else ''}). Check UMAMI_API_KEY, and that "
                "the Umami Cloud plan on this account includes API access."
            )
        return f"Umami returned {resp.status_code}{': ' + detail if detail else ''}"
    return None


def _umami_json(resp: httpx.Response, fallback):
    try:
        return resp.json()
    except Exception:
        return fallback


@router.get("/customers", response_model=CustomerBreakdown)
async def get_customers(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
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


@router.get("/revenue-breakdown", response_model=RevenueBreakdown)
async def get_revenue_breakdown(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Revenue split by delivery method and payment provider."""
    start, end = _date_range(start_date, end_date)

    cache_key = f"analytics:revenue-breakdown:{start}:{end}"
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

    payment_stmt = (
        select(
            Order.payment_provider.label("label"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.total), 0).label("revenue"),
        )
        .where(*base_filter)
        .group_by(Order.payment_provider)
        .order_by(func.sum(Order.total).desc())
    )
    payment_rows = (await db.execute(payment_stmt)).all()

    result_obj = RevenueBreakdown(
        by_delivery_method=[
            BreakdownItem(
                label=str(r.label), orders=int(r.orders), revenue=float(r.revenue)
            )
            for r in delivery_rows
        ],
        by_payment_provider=[
            BreakdownItem(
                label=str(r.label) if r.label else "unknown",
                orders=int(r.orders),
                revenue=float(r.revenue),
            )
            for r in payment_rows
        ],
    )
    await cache_set(cache_key, result_obj.model_dump(mode="json"), ttl=_ANALYTICS_TTL)
    return result_obj


@router.get("/zones", response_model=list[ZoneData])
async def get_zones(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
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
    _admin: User = Depends(get_admin_user),
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
