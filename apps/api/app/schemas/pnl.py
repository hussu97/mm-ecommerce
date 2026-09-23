"""
Response shapes for profit & loss — one order's, and a window's by channel.

Every money field is AED **net of VAT** and computed server-side by
`services/orders/order_pnl` (canon rule 10); the console renders these and
derives nothing. `float` for transport, like the other report schemas.
"""

from __future__ import annotations

from pydantic import BaseModel


class PnlStatement(BaseModel):
    """The P&L lines and subtotals, in statement order."""

    #: What the customer was billed before discounts, net of output VAT.
    gmv: float
    #: Partial refunds on an order that still stood.
    refunds: float
    net_revenue: float
    #: FIFO cost of the stock consumed. Null on a single order that drew none
    #: (before its branch's inventory go-live, or a sale that never posted).
    cogs: float | None
    #: Net revenue − COGS.
    pc1: float
    payment_fees: float
    #: Marketplace commission.
    commission: float
    #: Loyalty / Pro / Plus / delivery-subsidy fees the marketplace bills.
    marketplace_fees: float
    #: Our own courier's cost on a dispatched website order.
    delivery_cost: float
    aggregator_and_delivery_fees: float
    #: What a marketplace billed on a cancelled order, or its cancellation fee.
    cancellation_charges: float
    #: Non-order marketplace charges (report only; zero on an order).
    period_charges: float
    misc_fees: float
    #: PC1 − payment − aggregator & delivery − misc.
    pc2: float
    discounts: float
    #: PC2 − discounts.
    pc3: float
    #: Output VAT the orders collected for the FTA (after refunds).
    output_vat: float
    #: Input VAT on the cost lines that the booking entity reclaims.
    input_vat: float
    #: PC1–PC3 as a % of GMV; null when there is no GMV.
    pc1_pct: float | None
    pc2_pct: float | None
    pc3_pct: float | None


class OrderPnlResponse(PnlStatement):
    """One order's P&L, and what is still unknown about it."""

    order_number: str
    channel: str
    #: False on a cancelled order the shop was charged for: no revenue, only
    #: the charge.
    is_sale: bool
    #: COGS is unknown — the order drew no stock.
    cogs_missing: bool
    #: The part of COGS priced provisionally, awaiting a purchase order price.
    cogs_provisional: float
    #: A cost that will land later is still missing: a marketplace commission
    #: before its statement, or a courier that has not quoted or invoiced.
    fees_pending: bool


class OrderPnlBrief(BaseModel):
    """The orders list's one-cell summary of `OrderPnlResponse`."""

    gmv: float
    pc3: float
    pc3_pct: float | None
    is_sale: bool
    cogs_missing: bool
    fees_pending: bool


class PnlChannelColumn(PnlStatement):
    """One channel's (or the total's) P&L over the window."""

    #: A `order_pnl.CHANNELS` code, or `total`.
    channel: str
    orders: int
    #: Of `orders`, the cancelled ones the shop was charged for.
    charged_cancellations: int
    #: Of `orders`, how many carry a COGS figure.
    orders_with_cogs: int
    #: Of `orders`, how many are still missing a commission or courier cost.
    orders_fees_pending: int
    cogs_provisional: float


class PnlPeriodChargeRow(BaseModel):
    """One category of non-order marketplace charges in the window."""

    channel: str
    #: The marketplace's own fee word (`monthly_admin_fee`, `platform_fee`…).
    category: str
    description: str | None
    #: Net of VAT; positive is a cost, negative a credit.
    amount: float
    input_vat: float
    first_date: str
    last_date: str
    lines: int
    #: The statement fee less what its orders already carry (noon).
    is_true_up: bool


class PnlVatSummary(BaseModel):
    """The VAT the P&L's figures are net of."""

    output_vat: float
    input_vat_recoverable: float
    #: Output − recoverable input: what these sales leave owing to the FTA.
    #: Excludes input VAT on raw-goods purchases, which the VAT report books
    #: when the stock is bought, not when it is sold.
    net_vat_payable: float


class PnlReportResponse(BaseModel):
    date_from: str
    date_to: str
    #: One column per channel that had P&L activity, in display order.
    channels: list[PnlChannelColumn]
    total: PnlChannelColumn
    period_charges: list[PnlPeriodChargeRow]
    #: False when a branch or legal-entity filter is set: period charges are
    #: billed per marketplace account, not per branch, so they are left out
    #: rather than guessed at.
    period_charges_included: bool
    vat: PnlVatSummary
