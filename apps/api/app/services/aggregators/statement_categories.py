"""One source of truth for which verbatim marketplace words fall in which money
bucket on a settlement statement line.

The provider-neutral buckets — gross sales, commission, fee-VAT and net payout —
are recognised from the same `line_type` / `fee_category` vocabulary in two
places: the Fees & VAT roll-up (`aggregators.fees_summary`) that *reports* the
settled economics, and the settlement back-fill (`ingest`) that *writes* those
settled figures back onto the order feed. They must classify a line identically
or the number a report shows can drift from the number an order carries, so the
predicates live here once and both import them.

Each predicate takes the line model (or an alias) and returns a SQLAlchemy
boolean over its columns. VAT is deliberately testable first so a
``commission_vat`` line lands in VAT, not commission.
"""

from __future__ import annotations

from sqlalchemy import or_

from app.models.aggregator import AggregatorStatementLine


def _lt(col):
    from sqlalchemy import func

    return func.lower(func.coalesce(col.line_type, ""))


def _fc(col):
    from sqlalchemy import func

    return func.lower(func.coalesce(col.fee_category, ""))


def is_vat(col=AggregatorStatementLine):
    """Fee VAT — the tax on a fee, itemised on its own line (Careem/Deliveroo/
    Talabat). Matched before commission so ``commission_vat`` is VAT, not fee."""
    return or_(_lt(col) == "vat", _fc(col).like("%vat%"))


def is_commission(col=AggregatorStatementLine):
    """The marketplace commission line, excluding its VAT."""
    return (_fc(col) == "commission") & ~is_vat(col)


def is_commission_or_vat(col=AggregatorStatementLine):
    """The commission AND its own VAT — the pair that sums to the VAT-INCLUSIVE
    commission the order feed stores in `aggregator_order.commission_amount`
    (migration ``251_noon_commission_incl``). Two shapes in the wild, both handled
    by summing these two categories and taking the magnitude: noon books one
    `commission` line already VAT-inclusive (no `commission_vat` line), while
    Talabat/Careem/Deliveroo split it into an ex-VAT `commission` line plus a
    `commission_vat` line. Deliberately keys on `commission`/`commission_vat`
    ONLY — never the broad `is_vat` — so a `payment_handling_vat` line is not
    mistaken for commission VAT. Verified equal to the sales-feed value on live
    noon and Talabat orders (2026-09-22)."""
    return _fc(col).in_(["commission", "commission_vat"])


def is_payment_fee_or_vat(col=AggregatorStatementLine):
    """The per-order payment fee and its own VAT — the pair that sums to the
    VAT-INCLUSIVE `aggregator_order.payment_fee`. noon books one `payment_fee`
    line per order, already VAT-inclusive. Keys on these two categories only, so
    Careem/Talabat `payment_handling` lines (already on the order feed) are
    untouched."""
    return _fc(col).in_(["payment_fee", "payment_fee_vat"])


def is_cancellation_fee_or_vat(col=AggregatorStatementLine):
    """The per-order cancellation fee and its own VAT, summing to the
    VAT-INCLUSIVE `aggregator_order.cancellation_fee` (noon: one inclusive line)."""
    return _fc(col).in_(["cancellation_fee", "cancellation_fee_vat"])


def is_gross(col=AggregatorStatementLine):
    """The order's gross sale line (the customer-facing subtotal)."""
    return or_(
        _fc(col) == "gross_sales", _lt(col).in_(["gross_sales", "sales", "sale"])
    )


def is_net(col=AggregatorStatementLine):
    """The net-payable / settlement / payout line — what the marketplace actually
    pays for the order. Its sign is meaningful (a clawback period goes negative),
    so callers sum it SIGNED, never abs()'d."""
    return or_(
        _fc(col) == "net_payable",
        _lt(col).in_(["net_payable", "payout", "settlement"]),
    )


def is_other_revenue(col=AggregatorStatementLine):
    """Money that comes IN to the merchant — a Keeta-fault compensation, an upward
    adjustment, a Deliveroo invoice-correction credit — NOT a fee. `net_payable`
    already includes it, so treating it as a fee would double-count and inflate the
    take. Two ways a line is inbound: a known credit category, OR any `adjustment`
    line with a POSITIVE amount (Deliveroo's adjustment category is free text, so
    key off the sign — safe because every channel books its fees negative)."""
    return or_(
        _fc(col).in_(["merchant_compensation", "adjustment_increase"]),
        (_lt(col) == "adjustment") & (col.amount > 0),
    )


def is_refund(col=AggregatorStatementLine):
    """A refund line — money that left the SALE (a customer refund, a vendor-
    liability reversal), NOT a marketplace fee. Kept out of the fee take; the refund
    value is sourced from `aggregator_order.refund_amount` instead."""
    return _fc(col).in_(["customer_refund", "merchant_liability"])


def is_fee(col=AggregatorStatementLine):
    """Any cost-side deduction — commission AND every other fee, together. The
    residual after the non-fee line kinds are removed: not gross, not net, not VAT,
    not inbound revenue, not a refund. Summed as a magnitude, this is the
    statement's `total_fees` (VAT excluded, which is `is_vat`)."""
    return ~(
        is_gross(col)
        | is_net(col)
        | is_vat(col)
        | is_other_revenue(col)
        | is_refund(col)
    )
