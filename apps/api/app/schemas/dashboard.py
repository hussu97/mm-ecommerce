"""
Response shapes for the admin home dashboard (`/dashboard/today`).

One live snapshot of the trading day: the day's takings across every channel,
the mix behind them, and the open operational work an admin acts on now. Kept
here rather than beside the route so a reader learning the shape of the API
finds it with every other schema (CLAUDE.md rule #11).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BreakdownRow(BaseModel):
    """One slice of the day — a status, a channel, a fulfilment or a payment."""

    label: str
    orders: int
    revenue: float
    #: The raw grouping value, present on the *selector* breakdowns (branch,
    #: legal entity) so the client can toggle that filter by id. Null for the
    #: mixes whose cards are not selectors, and for an unattributed bucket.
    code: str | None = None


class SeriesPoint(BaseModel):
    """One point on the sales/orders trend line.

    `bucket` is the shop-local start of the interval (ISO 8601) — an hour for the
    live day or a single-day range, a calendar day for a multi-day range. Every
    interval in the window is present, zero-filled, so the line is continuous.
    """

    bucket: str
    orders: int
    revenue: float


class HeatmapCell(BaseModel):
    """One day-of-week × hour-of-day cell of the sales heatmap.

    `dow` is the shop-local day of week as PostgreSQL `extract(dow …)` numbers it
    — 0 = Sunday through 6 = Saturday — and `hour` is the shop-local hour 0–23.
    Both are on the shop's own clock (`created_at` is UTC, shifted into the shop
    timezone before extraction), so "hour of day" reads in Gulf time. Only cells
    with at least one order are emitted; the grid is zero-filled on the client.
    The window and status/courier selection match every other figure.
    """

    dow: int
    hour: int
    orders: int
    revenue: float


class CourierBreakdownRow(BaseModel):
    """One carrier's delivered orders and revenue — a courier scorecard.

    A carrier code (`counter`, an aggregator marketplace, or a dispatch courier),
    its display name and logo, and its **delivered** count and revenue. Delivered
    only, deliberately: this section answers "how much did each courier actually
    complete", so cancellations and in-progress orders are out — it is a
    settled-money view, unlike `by_status` which is the whole spread.
    """

    code: str
    label: str
    logo_url: str | None = None
    orders: int
    revenue: float
    #: What this carrier's orders cost us, as a percentage of their revenue —
    #: the fee rate on the tile. All VAT-inclusive, summed from the fees already
    #: stamped on each order (`order_fees.stamp`) plus the courier's own charge:
    #: an aggregator's marketplace commission + cancellation + merchant-funded
    #: marketing, a website courier's actual run cost (`order_deliveries.cost_total`
    #: / `quoted_cost`), and the payment-processing fee on every card/prepaid order
    #: whatever the carrier. Null when the carrier's revenue in the window is zero
    #: (no rate off nothing).
    fee_rate: float | None = None
    #: Whether some orders in this carrier's window still lack their dominant cost
    #: of sale, so `fee_rate` is a floor, not the final figure. True for an
    #: aggregator whose settlement statement is not scraped yet (commission null —
    #: noon settles weekly, Careem monthly), or a dispatched website order the
    #: courier has neither invoiced nor quoted. The tile shows "fees pending"
    #: rather than a low partial rate that reads as final.
    fee_rate_pending: bool = False


class DashboardSummary(BaseModel):
    """The day's headline figures, over every non-cancelled order created today."""

    orders: int
    revenue: float
    avg_order_value: float
    #: Of today's orders, how many have reached `delivered`.
    delivered: int
    #: Known VAT-inclusive cost of sale: marketplace and payment fees plus the
    #: courier's invoiced (or quoted) delivery cost.
    total_fees: float
    #: Whether some orders still lack their dominant cost, making total_fees and
    #: fee_rate a floor rather than the final number for the window.
    fees_pending: bool
    #: Known total fees as a percentage of revenue.
    fee_rate: float
    #: Percentage change against the same elapsed window yesterday. `0.0` when
    #: yesterday's figure was zero — there is no growth rate off nothing.
    orders_growth: float
    revenue_growth: float
    avg_order_value_growth: float
    delivered_growth: float
    total_fees_growth: float


class DashboardOps(BaseModel):
    """
    Open work, as it stands right now.

    These are current-state counts, not windowed to today, because an order that
    went out for delivery last night is still the shop's problem this morning —
    except the three explicitly named `_today`, which are the day's events.
    """

    #: Paid, out with a rider, not yet handed over.
    out_for_delivery: int
    #: A rider reached the door and could not deliver — needs re-dispatch.
    undelivered: int
    payment_failed_today: int
    refunds_today: int
    refunds_amount_today: float
    #: Custom-cake orders still in the pipeline (enquiry → ready).
    open_custom_orders: int
    custom_orders_due_today: int
    #: Stock levels below their item's minimum.
    low_stock_items: int
    pending_purchase_orders: int
    open_tills: int
    active_couriers: int


class DashboardTodayResponse(BaseModel):
    #: The shop's local calendar date the figures cover (ISO 8601) — the range
    #: start when a range was requested, else the single trading day.
    business_date: str
    #: The range end (ISO 8601), or null for the live single-day view.
    business_date_to: str | None = None
    timezone: str
    generated_at: datetime
    summary: DashboardSummary
    #: Every status present today, cancellations and refunds included.
    by_status: list[BreakdownRow]
    #: By carrier — counter, each aggregator marketplace, each dispatch courier —
    #: over delivered orders only. The full courier menu, like `by_status`.
    by_courier: list[CourierBreakdownRow]
    #: By branch, over revenue-eligible orders across every channel. Every order
    #: resolves to a branch (`orders.branch_id` is NOT NULL), so there is no
    #: unattributed bucket.
    by_branch: list[BreakdownRow]
    #: By legal entity the order was billed under, over revenue-eligible orders.
    #: The full entity menu (a selector, like `by_branch`); `legal_entity_id` is
    #: nullable, so a non-registered counter sale falls in an "Unknown" bucket
    #: with a null `code`.
    by_legal_entity: list[BreakdownRow]
    #: By product category, over the orders' lines (item-level): revenue is the
    #: sum of the matching lines' `total_price` and `orders` the distinct count of
    #: orders touching the category, so an order spanning categories is counted in
    #: each. A selector, like `by_branch`; a line with no category (or no product)
    #: falls in an "Uncategorised" bucket with a null `code`.
    by_category: list[BreakdownRow]
    #: By order source — storefront, cashier, aggregator.
    by_channel: list[BreakdownRow]
    #: Delivery vs pickup.
    by_fulfillment: list[BreakdownRow]
    #: Card vs cash on delivery.
    by_payment: list[BreakdownRow]
    #: Orders and revenue over time, one point per interval across the window,
    #: following the same status/courier selection as every other figure.
    series: list[SeriesPoint]
    #: The interval each `series` point spans: `hour` (live day / single day) or
    #: `day` (multi-day range). Drives the axis and tooltip formatting.
    series_granularity: str
    #: Orders and revenue aggregated by shop-local day-of-week × hour-of-day over
    #: the whole window — the heatmap under each trend chart. Sparse: only cells
    #: with orders are present.
    heatmap: list[HeatmapCell]
    ops: DashboardOps
