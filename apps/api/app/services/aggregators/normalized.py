"""The channel-neutral shapes a provider returns.

Careem speaks REST, Talabat speaks two GraphQLs, Deliveroo hands back a CSV and
Keeta a signed JSON page — and none of that reaches the ingest. Each provider
translates its marketplace into these dataclasses at the edge, the way the
payment gateways translate their webhooks into one `GatewayEvent`, so the code
that writes `aggregator_order` / `aggregator_statement` reads one vocabulary.

Money is `Decimal | None`. None means "the marketplace did not tell us", which
is not zero: a null commission is "unknown", a zero commission is "charged
nothing", and a reconciliation that confuses the two lies about the fee.

Modifiers are first-class: each option carries a name **and** a quantity (default
1 only when the portal omits it — never invent qty by repeating rows).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.models.aggregator import GRAIN_LINE, STATEMENT_GRAIN_ORDER


@dataclass(frozen=True)
class StandardModifier:
    """One chosen option on a sold line — name + quantity are both load-bearing."""

    name: str
    #: Portal qty/count. Default to 1 only when the source truly omitted it.
    quantity: Decimal = Decimal("1")
    unit_price: Decimal | None = None
    #: Portal option / modifier code when present — feeds `external_item_map`.
    external_ref: str | None = None


@dataclass(frozen=True)
class StandardOrderItem:
    """One sold line, or a period-window aggregate of one item (see `grain`)."""

    source_key: str
    grain: str = GRAIN_LINE
    item_name: str | None = None
    category_name: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    gross_sales: Decimal | None = None
    net_sales: Decimal | None = None
    #: False when the channel gives a name/quantity but no per-line money — a
    #: zero there would read as free.
    amount_is_known: bool = True
    #: Structured options with qty; prefer this over `modifiers_text`.
    modifiers: list[StandardModifier] = field(default_factory=list)
    #: Raw portal dump for debugging when structure is lossy.
    modifiers_text: str | None = None
    business_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None


@dataclass(frozen=True)
class StandardOrder:
    """One order as a marketplace's ledger holds it — the sales truth."""

    external_order_id: str
    #: The marketplace's own outlet id, resolved to a branch by the ingest via
    #: `aggregator_branch_map`.
    external_outlet_id: str | None = None
    business_date: str | None = None
    placed_at: datetime | None = None
    accepted_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    status: str | None = None
    currency: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    gross_sales: Decimal | None = None
    net_sales: Decimal | None = None
    commission_amount: Decimal | None = None
    payment_fee: Decimal | None = None
    delivery_fee: Decimal | None = None
    vat_amount: Decimal | None = None
    cancellation_fee: Decimal | None = None
    refund_amount: Decimal | None = None
    net_payable: Decimal | None = None
    statement_id: str | None = None
    items: list[StandardOrderItem] = field(default_factory=list)
    raw: dict | None = None


@dataclass(frozen=True)
class StandardStatementLine:
    """One line of a settlement — a sale credit, a fee, a refund, an adjustment."""

    source_key: str
    statement_id: str | None = None
    transfer_id: str | None = None
    external_order_id: str | None = None
    line_date: str | None = None
    line_type: str | None = None
    fee_category: str | None = None
    description: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    #: `order` when keyed to an external_order_id; `summary` for period totals.
    grain: str = STATEMENT_GRAIN_ORDER


@dataclass(frozen=True)
class StandardStatement:
    """A published settlement summary over one period."""

    statement_id: str
    period_start: str | None = None
    period_end: str | None = None
    payment_due_date: str | None = None
    gross_sales: Decimal | None = None
    net_payable: Decimal | None = None
    total_fees: Decimal | None = None
    total_vat: Decimal | None = None
    currency: str | None = None
    #: Marketplace outlet when the statement is per-branch (Talabat detailed).
    external_outlet_id: str | None = None
    #: Populated when the provider archives the settlement invoice bytes.
    invoice_object_key: str | None = None
    invoice_content_type: str | None = None
    invoice_original_filename: str | None = None
    invoice_fetched_at: datetime | None = None
    invoice_attachments: list[dict] | None = None
    lines: list[StandardStatementLine] = field(default_factory=list)
    raw: dict | None = None


@dataclass(frozen=True)
class StandardPayout:
    """A transfer the marketplace made or scheduled against a statement."""

    transfer_id: str
    statement_id: str | None = None
    transfer_date: str | None = None
    payment_due_date: str | None = None
    transfer_amount: Decimal | None = None
    transfer_status: str | None = None
    payment_reference: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class SalesResult:
    """What a `fetch_sales` returns: the orders, and what was truncated."""

    orders: list[StandardOrder] = field(default_factory=list)
    #: A human note when a date-window or page cap stopped the pull short of the
    #: requested range, so the sync run records it rather than implying it
    #: covered everything.
    truncation_note: str | None = None


@dataclass(frozen=True)
class StatementsResult:
    """What a `fetch_statements` returns."""

    statements: list[StandardStatement] = field(default_factory=list)
    truncation_note: str | None = None


@dataclass(frozen=True)
class PayoutsResult:
    """What a `fetch_payouts` returns."""

    payouts: list[StandardPayout] = field(default_factory=list)
    truncation_note: str | None = None


@dataclass(frozen=True)
class FinanceResult:
    """Combined statements + payouts (compat wrapper over the split methods)."""

    statements: list[StandardStatement] = field(default_factory=list)
    payouts: list[StandardPayout] = field(default_factory=list)
    truncation_note: str | None = None
