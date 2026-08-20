from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.cache import cache_get, cache_set, cache_delete_pattern  # noqa: F401
from app.core import search as search_text
from app.core.config import settings
from app.core.deps import get_db
from app.core.permissions import require
from app.models.cart import Cart, CartItem
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.user import User
from app.services import (
    business_day_service,
    cart_service,
    delivery_service,
    order_pricing,
)

logger = logging.getLogger(__name__)

#: How stale the console's figures may be, and the only thing that makes them
#: fresh again.
#:
#: Five minutes, expiring on its own — there is deliberately no invalidation.
#: Five things move these numbers (a checkout, a payment webhook confirming
#: one, an admin changing a status, a refund, and every sale at a till), and
#: for a while exactly one of them dropped these keys, which is worse than none
#: dropping them: it read as a freshness guarantee that four writers out of
#: five quietly broke. Busting on all five would mean a Redis keyspace scan on
#: every counter sale to save a margin dashboard five minutes, which is not a
#: trade worth making. So: one rule, same for every writer, stated here.
_ANALYTICS_TTL = 300

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _date_range(
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[date, date]:
    if not end_date:
        end_date = business_day_service.shop_today()
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

    #: How customers chose to pay: `card` or `cod`.
    #:
    #: This is the commercial question — what share of takings comes in at the
    #: counter versus on a card — and it is stable no matter which processor is
    #: carrying the card estate this week.
    by_payment_method: list[BreakdownItem]

    #: Which processor settled the card orders: `stripe` or `ziina`.
    #:
    #: Card only. Cash is deliberately excluded rather than shown as a third
    #: slice, because "cash" is not a gateway and putting it here makes the
    #: chart answer neither question — which is what the old combined breakdown
    #: did, back when there was only one processor and it did not show.
    by_payment_gateway: list[BreakdownItem]

    #: The previous name for the combined `{stripe, cod}` split.
    #:
    #: Kept so a dashboard or export built against it does not break mid-deploy.
    #: It now carries the same rows as `by_payment_method`, which is what it
    #: always meant to whoever was reading it: with one processor, "provider"
    #: and "method" were the same word.
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


@router.get("/funnel", response_model=FunnelData)
async def get_funnel(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("reports.sales")),
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
    # `arrived_at_pos` counts as confirmed here. The bucket means "paid for and
    # not yet finished", and an order waiting for its run is exactly that —
    # splitting it out would put a step in a funnel about *checkout* that is
    # really about our dispatch schedule. Folding it in also keeps `total`
    # whole: left out, every batched order would vanish from the denominator
    # and quietly flatter the conversion rate.
    confirmed = counts.get(OrderStatusEnum.CONFIRMED, 0) + counts.get(
        OrderStatusEnum.ARRIVED_AT_POS, 0
    )
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
    _admin: User = Depends(require("reports.sales")),
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


# ─── Live baskets ─────────────────────────────────────────────────────────────
#
# Everything above answers a question about the past, off `orders`, and is
# cached for five minutes because the past does not move. This section answers
# the opposite question — what is in people's hands *right now* — and is
# deliberately outside that arrangement:
#
# * **Uncached.** A basket abandoned four minutes ago shown as still active is
#   the one error this screen cannot afford, because the whole point of it is
#   the age of the row. `_ANALYTICS_TTL` would put a five-minute lie on the only
#   column anybody reads.
# * **`customers.read`, not `reports.sales`.** The rest of the dashboard is
#   aggregate; this names individual people and carries their email addresses.
#   The permission that governs "see who a customer is" is the one that should
#   open it — the sales-report permission was never granted with that in mind.


class LiveCartLine(BaseModel):
    """One line of a basket somebody is still holding."""

    product_id: uuid.UUID
    product_name: str
    product_sku: str | None = None
    quantity: int
    #: Product plus the options chosen on it, priced by the same
    #: `cart_service.line_unit_price` the storefront basket renders from.
    unit_price: float
    line_total: float
    #: The chosen options, as words: `["Filling: Ferrero ×2", "Size: 9\""]`.
    #: Read off the line's own snapshot, so it says what was chosen at the time
    #: even if the modifier has since been renamed.
    options: list[str] = []
    #: What the customer asked to have written, for a gift line.
    personalisation_note: str | None = None


class LiveCart(BaseModel):
    """
    One basket that currently holds items, and everything needed to judge it.
    """

    id: uuid.UUID
    #: The account holding it, when there is one.
    user_id: uuid.UUID | None = None
    #: The browser holding it, when there is not.
    session_id: str | None = None

    #: Where to write, or `null` for a basket nobody can be reached about.
    #:
    #: The account's address where the basket has a `user_id`, otherwise the one
    #: typed into the checkout form. Never both — see `Cart.guest_email`.
    email: str | None = None
    #: `account` or `checkout`, saying which of the two the address above came
    #: from. `null` when there is no address. Worth showing: a `checkout`
    #: address means the shopper reached the form and turned back, which is a
    #: different and much warmer thing than a basket left on a product page.
    email_source: str | None = None
    #: False for a basket held by a browser with no account, and for the guest
    #: account a checkout mints — neither has a password or an order history.
    #:
    #: There is no name to show alongside: `users` carries an email and a phone
    #: for a customer and nothing else. A name is typed per delivery address,
    #: and a basket has no address yet — which is most of why it is still a
    #: basket.
    is_registered: bool = False

    lines: list[LiveCartLine] = []
    #: Units, not lines: two of one cake and one of another is three.
    item_count: int = 0

    #: Goods only. No fees, no VAT, no discount.
    subtotal: float
    #: The small-basket surcharge this basket would attract on a delivery,
    #: from `order_pricing.low_order_fee_for` — the same function that charges
    #: it. Zero above the threshold.
    low_order_fee: float = 0.0
    #: What a courier quoted for this basket while the shopper was deciding, if
    #: they got as far as dropping a pin. Null otherwise: there is no honest
    #: delivery fee for an address nobody has given yet, and inventing one would
    #: put a number on this screen the shop never quoted.
    delivery_fee: float | None = None
    #: The zone that estimate was taken against, for the same reason.
    delivery_zone: str | None = None
    #: `subtotal + low_order_fee + delivery_fee`, with the fees that are known.
    #:
    #: **Excludes any promo discount**, including when `promo_code` is set. What
    #: a coupon is worth depends on the basket, the identity and the day, and it
    #: is decided at the checkout every time it is asked (migration 115). A
    #: figure here would be a second answer, quoted from a screen that cannot
    #: see the first.
    estimated_total: float

    #: The code applied in the basket. The code, never the discount.
    promo_code: str | None = None

    created_at: datetime
    #: When the shopper last touched this basket. See `Cart.last_activity_at`.
    last_activity_at: datetime | None = None
    #: How long it has been sitting untouched, in whole minutes. Computed here
    #: so every reader agrees on it and so the table can be sorted and filtered
    #: on one number rather than on a clock difference each client works out.
    idle_minutes: int | None = None


class LiveCartsSummary(BaseModel):
    """
    The header figures — and, read together, the abandoned-cart business case.

    `reachable_value` against `total_value` is the whole question: it is what
    a recovery email could be sent about, as against what is sitting there.
    Both are over the filtered set, so narrowing to "idle more than an hour"
    narrows these too, which is the comparison worth making.
    """

    carts: int
    #: Goods value across every basket matching the filters.
    total_value: float
    #: How many of them carry an address.
    with_email: int
    #: Goods value of just those.
    reachable_value: float
    idle_over_1h: int
    idle_over_24h: int


class LiveCartsResponse(BaseModel):
    items: list[LiveCart]
    total: int
    page: int
    per_page: int
    pages: int
    summary: LiveCartsSummary


def _option_words(option: dict) -> str:
    """One snapshot row as the console should read it."""
    modifier = option.get("modifier_name") or ""
    name = option.get("option_name") or "?"
    quantity = int(option.get("quantity") or 1)
    label = f"{modifier}: {name}" if modifier else name
    return f"{label} ×{quantity}" if quantity > 1 else label


def _cart_email(cart: Cart) -> tuple[str | None, str | None]:
    """
    Where to write about this basket, and where that address came from.

    The account first. `Cart.guest_email` is only ever written for a basket
    without one, so in practice these never compete — the precedence is stated
    here so that a row restored from a dump that predates that rule still
    answers with the address that cannot be stale.
    """
    if cart.user is not None and cart.user.email:
        return cart.user.email, "account"
    if cart.guest_email:
        return cart.guest_email, "checkout"
    return None, None


#: When a basket was last touched, with `updated_at` as the fallback.
#:
#: The fallback covers rows written before `last_activity_at` existed and any
#: the backfill in migration 116 could not reach. Written once and used in the
#: filter, the sort and the response, so a basket cannot be selected by one
#: clock and then displayed against another.
_ACTIVITY = func.coalesce(Cart.last_activity_at, Cart.updated_at)


def live_carts_query(
    now: datetime,
    *,
    search: str | None = None,
    has_email: bool | None = None,
    idle_minutes_min: int = 0,
    idle_days_max: int = 30,
):
    """
    The baskets holding items, newest activity first, as a statement.

    Its own function so a test can compile it against PostgreSQL. Everything
    here — a partial index's `NULLS LAST`, an `EXISTS` over `cart_items`, a
    `contains_eager` hung off an outer join — is the kind of thing that is fine
    in Python and wrong in SQL, and a handler that builds its query inline can
    only be checked against a database.

    `min_value` is deliberately **not** here. A line's price is the product's
    base plus a JSONB snapshot whose free-allowance rule lives in
    `cart_service.option_charge`; expressing that in SQL would be a second
    implementation of money math this module promises not to write. It is
    applied to the loaded rows instead, over a set bounded by `idle_days_max`.
    """
    base = (
        select(Cart)
        .outerjoin(User, User.id == Cart.user_id)
        .where(
            # "Current carts *with items*". An empty basket is a row the
            # storefront made on a page load, not a shopper who chose anything.
            Cart.items.any(),
            _ACTIVITY >= now - timedelta(days=idle_days_max),
            _ACTIVITY <= now - timedelta(minutes=idle_minutes_min),
        )
    )

    if has_email is True:
        base = base.where(or_(User.email.isnot(None), Cart.guest_email.isnot(None)))
    elif has_email is False:
        base = base.where(User.email.is_(None), Cart.guest_email.is_(None))

    if search:
        term = search.strip()
        base = base.where(
            or_(
                search_text.contains(User.email, term),
                search_text.contains(Cart.guest_email, term),
                search_text.contains(Cart.session_id, term),
            )
        )

    return base.order_by(_ACTIVITY.desc().nullslast(), Cart.id.desc()).options(
        # `contains_eager`, not `joinedload`: the outer join to `users` is
        # already in the statement because the filters above read `User.email`,
        # and a `joinedload` would add a second, aliased copy of the same join
        # to populate `cart.user` from.
        contains_eager(Cart.user),
        selectinload(Cart.items).joinedload(CartItem.product),
    )


@router.get("/live-carts", response_model=LiveCartsResponse)
async def get_live_carts(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=2000),
    search: str | None = Query(
        None, description="Match an email address or a session id"
    ),
    has_email: bool | None = Query(
        None, description="Only baskets we can (or cannot) write to"
    ),
    min_value: float | None = Query(
        None, ge=0, description="Only baskets worth at least this, in goods"
    ),
    idle_minutes_min: int = Query(
        0, ge=0, description="Only baskets untouched for at least this long"
    ),
    idle_days_max: int = Query(
        30,
        ge=1,
        le=365,
        description="Ignore baskets untouched for longer than this",
    ),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("customers.read")),
):
    """
    Every basket that currently holds items, most recently touched first.

    What the shop could not see before this endpoint: that eleven people are
    holding four thousand dirhams of cake right now, that six of them stopped
    two hours ago, and that four of those six left an address on the way past
    the checkout. That last number is the abandoned-cart business case, and
    `summary` states it directly.

    **Every figure is the server's.** The subtotal is
    `cart_service.line_total` — the same formula the storefront basket renders
    from — and the surcharge is `order_pricing.low_order_fee_for`, the function
    that charges it. There is no arithmetic on the console side and no second
    copy of either rule here (CLAUDE.md rule 10).

    **The delivery fee is a quote, not a price.** It is what a courier said this
    basket would cost to the pin the shopper had dropped, captured by the
    checkout preview while they were still deciding. A basket that never reached
    an address shows nothing rather than a guess.

    `idle_days_max` is a floor under the query rather than a preference: `carts`
    has no expiry, so without it this grows to every basket ever abandoned and
    the page nobody wants is the one the database works hardest for.
    """
    now = datetime.now(timezone.utc)
    stmt = live_carts_query(
        now,
        search=search,
        has_email=has_email,
        idle_minutes_min=idle_minutes_min,
        idle_days_max=idle_days_max,
    )
    carts = (await db.execute(stmt)).unique().scalars().all()

    settings_row = await delivery_service.get_settings(db)

    rows: list[LiveCart] = []
    for cart in carts:
        subtotal = cart_service.subtotal_of(cart)
        if min_value is not None and subtotal < Decimal(str(min_value)):
            continue

        email, email_source = _cart_email(cart)
        last_activity = cart.last_activity_at or cart.updated_at
        idle_minutes = (
            int((now - last_activity).total_seconds() // 60)
            if last_activity is not None and last_activity.tzinfo is not None
            else None
        )

        low_order_fee = order_pricing.low_order_fee_for(
            subtotal, DeliveryMethodEnum.DELIVERY, settings_row
        )
        delivery_fee = cart.delivery_quote_fee

        rows.append(
            LiveCart(
                id=cart.id,
                user_id=cart.user_id,
                session_id=cart.session_id,
                email=email,
                email_source=email_source,
                is_registered=cart.user is not None and not cart.user.is_guest,
                lines=[
                    LiveCartLine(
                        product_id=item.product_id,
                        product_name=(
                            item.product.name if item.product else "(deleted product)"
                        ),
                        product_sku=item.product.sku if item.product else None,
                        quantity=item.quantity,
                        unit_price=float(cart_service.line_unit_price(item)),
                        line_total=float(cart_service.line_total(item)),
                        options=[
                            _option_words(opt) for opt in (item.selected_options or [])
                        ],
                        personalisation_note=item.personalisation_note,
                    )
                    for item in cart.items
                ],
                item_count=sum(item.quantity for item in cart.items),
                subtotal=float(subtotal),
                low_order_fee=float(low_order_fee),
                delivery_fee=float(delivery_fee) if delivery_fee is not None else None,
                delivery_zone=cart.delivery_quote_zone,
                estimated_total=float(
                    subtotal + low_order_fee + (delivery_fee or Decimal("0.00"))
                ),
                promo_code=cart.promo_code,
                created_at=cart.created_at,
                last_activity_at=last_activity,
                idle_minutes=idle_minutes,
            )
        )

    # Over the whole filtered set, not the page — a header that changed as you
    # paged through it would be answering a question nobody asked.
    summary = LiveCartsSummary(
        carts=len(rows),
        total_value=round(sum(row.subtotal for row in rows), 2),
        with_email=sum(1 for row in rows if row.email),
        reachable_value=round(sum(row.subtotal for row in rows if row.email), 2),
        idle_over_1h=sum(
            1 for row in rows if row.idle_minutes is not None and row.idle_minutes >= 60
        ),
        idle_over_24h=sum(
            1
            for row in rows
            if row.idle_minutes is not None and row.idle_minutes >= 1440
        ),
    )

    total = len(rows)
    start = (page - 1) * per_page
    return LiveCartsResponse(
        items=rows[start : start + per_page],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, (total + per_page - 1) // per_page),
        summary=summary,
    )
