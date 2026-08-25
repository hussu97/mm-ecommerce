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


class DashboardSummary(BaseModel):
    """The day's headline figures, over every non-cancelled order created today."""

    orders: int
    revenue: float
    avg_order_value: float
    #: Of today's orders, how many have reached `delivered`.
    delivered: int
    #: Percentage change against the same elapsed window yesterday. `0.0` when
    #: yesterday's figure was zero — there is no growth rate off nothing.
    orders_growth: float
    revenue_growth: float


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
    #: The shop's local calendar date the figures cover (ISO 8601).
    business_date: str
    timezone: str
    generated_at: datetime
    summary: DashboardSummary
    #: Every status present today, cancellations and refunds included.
    by_status: list[BreakdownRow]
    #: By order source — storefront, cashier, aggregator.
    by_channel: list[BreakdownRow]
    #: Delivery vs pickup.
    by_fulfillment: list[BreakdownRow]
    #: Card vs cash on delivery.
    by_payment: list[BreakdownRow]
    ops: DashboardOps
