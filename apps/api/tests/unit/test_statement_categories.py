"""The statement-line category vocabulary is a shared contract — the Fees & VAT
roll-up and the settlement back-fill both classify a line through these
predicates, so a silent change to which verbatim word lands in which bucket would
make a reported fee and a written-back order fee disagree. Pin the vocabulary by
compiling each predicate to SQL and asserting the words it keys off, with no DB.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.services.aggregators import statement_categories as sc


def _sql(expr) -> str:
    return str(
        expr.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def test_net_recognises_every_payout_word():
    sql = _sql(sc.is_net())
    for word in ("net_payable", "payout", "settlement"):
        assert word in sql


def test_gross_recognises_every_sale_word():
    sql = _sql(sc.is_gross())
    for word in ("gross_sales", "sales", "sale"):
        assert word in sql


def test_vat_matches_the_vat_suffix_and_type():
    sql = _sql(sc.is_vat())
    assert "%vat%" in sql
    assert "vat" in sql


def test_commission_excludes_its_own_vat():
    # is_commission must AND-NOT is_vat, else a `commission_vat` line double-books.
    sql = _sql(sc.is_commission())
    assert "commission" in sql
    assert "not" in sql and "vat" in sql


def test_commission_or_vat_keys_on_both_categories_only():
    # The VAT-inclusive commission pair rolled onto aggregator_order.commission_amount
    # — must be exactly `commission` + `commission_vat`, never the broad is_vat (which
    # would sweep in payment_handling_vat and over-state the commission).
    sql = _sql(sc.is_commission_or_vat())
    assert "commission" in sql
    assert "commission_vat" in sql
    assert "payment_handling" not in sql
    assert "%vat%" not in sql  # not the broad LIKE match


def test_other_revenue_covers_credits_and_positive_adjustments():
    sql = _sql(sc.is_other_revenue())
    assert "merchant_compensation" in sql
    assert "adjustment_increase" in sql
    assert "adjustment" in sql  # the sign-based inbound rule


def test_refund_is_customer_refund_and_merchant_liability():
    sql = _sql(sc.is_refund())
    assert "customer_refund" in sql
    assert "merchant_liability" in sql


def test_fee_is_the_residual_of_the_non_fee_kinds():
    # A fee excludes gross, net, VAT, inbound revenue and refunds — so its SQL
    # negates all of them.
    sql = _sql(sc.is_fee())
    for word in (
        "net_payable",
        "gross_sales",
        "%vat%",
        "merchant_compensation",
        "customer_refund",
    ):
        assert word in sql
    assert "not" in sql
