"""
Every response shape the analytics console reads.

These were declared inline among the endpoints, with `RevenueBreakdown` and
friends at the top of the file and the live-basket ones 800 lines below. One
home, so a reader looking for the shape of a response does not have to know
which half of the screen it feeds.
"""

from __future__ import annotations

from pydantic import BaseModel

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
