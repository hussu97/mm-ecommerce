"""
Response shapes for profit & loss — one order's, and a window's by channel.

Revenue and cost lines are AED as billed (VAT included), with the VAT as lines
of its own, so every subtotal is net of VAT. All computed server-side by
`services/orders/order_pnl` (canon rule 10); the console renders these and
derives nothing. `float` for transport, like the other report schemas.
"""

from __future__ import annotations

from pydantic import BaseModel


class PnlStatement(BaseModel):
    """
    The P&L lines and subtotals, in statement order.

    Revenue and fees are as billed, VAT included, and their VAT comes off as
    lines of its own (`output_vat`, `fees_vat`); COGS is net cost. So every
    subtotal from `net_revenue` down is net of VAT.
    """

    #: The goods the customer was billed for before discounts, VAT included.
    #: Delivery fees are their own line (`delivery_fees`).
    gmv: float
    #: Partial refunds on an order that still stood, as refunded.
    refunds: float
    #: VAT on sales owed to the FTA — charged, less the VAT inside refunds.
    output_vat: float
    #: GMV − refunds − VAT on sales.
    net_revenue: float
    #: FIFO cost of the stock consumed, net of the VAT reclaimed when it was
    #: bought (assumed at the standard rate on all of it). Null on a single
    #: order that drew none (before its branch's inventory go-live, or a sale
    #: that never posted).
    cogs: float | None
    #: Net revenue − COGS.
    pc1: float
    #: Delivery + small-basket fees the customer paid us (website). Outside the
    #: VAT base, so no VAT line; the card fee on them is in `payment_fees`.
    delivery_fees: float
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
    #: Input VAT reclaimed on the fee lines — zero under a non-registered entity.
    fees_vat: float
    #: PC1 + delivery fees − payment − aggregator & delivery − misc + VAT
    #: reclaimed on fees.
    pc2: float
    #: As given, VAT included.
    discounts: float
    #: PC2 − discounts.
    pc3: float
    #: VAT on sales − VAT reclaimed on fees.
    net_vat: float
    #: PC1–PC3 as a % of GMV (VAT included); null when there is no GMV.
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
    #: As billed, VAT included; positive is a cost, negative a credit.
    amount: float
    #: The reclaimable VAT inside `amount`.
    input_vat: float
    first_date: str
    last_date: str
    lines: int
    #: The statement fee less what its orders already carry (noon).
    is_true_up: bool


class PnlVatSummary(BaseModel):
    """The VAT lines of the statement, in one place."""

    #: VAT on sales owed to the FTA, after refunds.
    output_vat: float
    #: VAT reclaimed on fees and period charges.
    fees_vat_reclaimed: float
    #: Output − reclaimed. Excludes VAT on the stock sold, which the VAT report
    #: reclaims when the stock is bought.
    net_vat: float


class PnlReportResponse(BaseModel):
    date_from: str
    date_to: str
    #: One column per channel that had P&L activity, in display order.
    channels: list[PnlChannelColumn]
    total: PnlChannelColumn
    period_charges: list[PnlPeriodChargeRow]
    #: False when a branch filter is set (period charges are billed per
    #: marketplace account, not per branch, so they are left out rather than
    #: guessed at), or when an entity filter excludes the entity marketplace
    #: orders are booked under.
    period_charges_included: bool
    vat: PnlVatSummary
