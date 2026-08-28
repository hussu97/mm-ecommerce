"""Unit coverage for Layer A settlement reconciliation — the pure aggregation
and variance logic, no DB.

The grouped SQL runs against Postgres in production; what is pinned here is the
arithmetic that would go wrong silently: the two per-statement variances, the
null-total handling (talabat file-rows report null and a `no_statement_total`
flag, never a false 0-variance), and the accumulated-payout rollup where two
statements sum to one transfer (8328.29 = 5046.48 + 3281.81).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.aggregators.settlement_reconcile import (
    PayoutInfo,
    build_payout_rollups,
    build_statement_recon,
)


def _statement(**over):
    base = dict(
        statement_id="ST1",
        period_start="2026-08-01",
        period_end="2026-08-07",
        payment_due_date="2026-08-10",
        currency="AED",
        net_payable=Decimal("5046.48"),
        payout_transfer_id="TR1",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── per-statement variances ──────────────────────────────────────────────────
def test_clean_statement_has_no_variance_flags():
    row = build_statement_recon(
        channel="noon",
        statement=_statement(),
        sales_total=Decimal("5046.48"),
        orders_count=12,
        orders_promoted=12,
        settled_total=Decimal("5046.48"),
        lines_count=12,
        payout=PayoutInfo("TR1", Decimal("8328.29"), "2026-08-10", "paid"),
    )
    assert row.sales_total == Decimal("5046.48")
    assert row.settled_total == Decimal("5046.48")
    assert row.sales_vs_settled == Decimal("0.00")
    assert row.settled_vs_statement == Decimal("0.00")
    assert row.sales_vs_settled_flag is False
    assert row.settled_vs_statement_flag is False
    assert row.orders_promoted == 12
    assert row.flags == []


def test_sales_vs_settled_variance_flags_when_over_tolerance():
    row = build_statement_recon(
        channel="noon",
        statement=_statement(net_payable=Decimal("5040.00"), payout_transfer_id=None),
        sales_total=Decimal("5046.48"),
        orders_count=12,
        orders_promoted=10,
        settled_total=Decimal("5040.00"),
        lines_count=11,
        payout=None,
    )
    assert row.sales_vs_settled == Decimal("6.48")
    assert row.sales_vs_settled_flag is True
    assert "sales_vs_settled_variance" in row.flags
    # Settled side agrees with the statement's own total → no second variance.
    assert row.settled_vs_statement_flag is False
    # No payout linked on this statement → surfaced, not silent.
    assert "no_payout_linked" in row.flags


def test_penny_difference_is_within_tolerance():
    row = build_statement_recon(
        channel="noon",
        statement=_statement(net_payable=Decimal("100.00")),
        sales_total=Decimal("100.01"),
        orders_count=1,
        orders_promoted=1,
        settled_total=Decimal("100.00"),
        lines_count=1,
        payout=PayoutInfo("TR1", Decimal("100.00"), "2026-08-10", "paid"),
    )
    assert row.sales_vs_settled == Decimal("0.01")
    assert row.sales_vs_settled_flag is False  # abs == tol, not > tol
    assert row.flags == []


def test_settled_vs_statement_variance_flags():
    row = build_statement_recon(
        channel="deliveroo",
        statement=_statement(net_payable=Decimal("200.00")),
        sales_total=Decimal("205.00"),
        orders_count=3,
        orders_promoted=3,
        settled_total=Decimal("205.00"),
        lines_count=3,
        payout=None,
    )
    assert row.settled_vs_statement == Decimal("5.00")
    assert row.settled_vs_statement_flag is True
    assert "settled_vs_statement_variance" in row.flags


# ── null = unknown, never a false zero ───────────────────────────────────────
def test_null_statement_total_reports_null_not_zero():
    """Talabat file-rows carry no net_payable: report null + a flag, not a 0
    variance."""
    row = build_statement_recon(
        channel="talabat",
        statement=_statement(net_payable=None),
        sales_total=Decimal("300.00"),
        orders_count=5,
        orders_promoted=0,
        settled_total=Decimal("290.00"),
        lines_count=5,
        payout=None,
    )
    assert row.statement_net_payable is None
    assert row.settled_vs_statement is None
    assert row.settled_vs_statement_flag is False
    assert "no_statement_total" in row.flags
    assert "settled_vs_statement_variance" not in row.flags
    # The sales↔settled check still fires — both sides are known there.
    assert row.sales_vs_settled == Decimal("10.00")
    assert row.sales_vs_settled_flag is True


def test_unknown_sales_side_yields_null_variance():
    row = build_statement_recon(
        channel="keeta",
        statement=_statement(),
        sales_total=None,
        orders_count=0,
        orders_promoted=0,
        settled_total=Decimal("5046.48"),
        lines_count=12,
        payout=PayoutInfo("TR1", None, "2026-08-10", "paid"),
    )
    assert row.sales_total is None
    assert row.sales_vs_settled is None
    assert row.sales_vs_settled_flag is False


# ── accumulated-payout rollup: two statements → one transfer ─────────────────
def test_payout_rollup_matches_accumulated_transfer():
    """One noon transfer of 8328.29 clears two statements (5046.48 + 3281.81)."""
    payout = PayoutInfo("TR1", Decimal("8328.29"), "2026-08-10", "paid")
    s1 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST1", net_payable=Decimal("5046.48")),
        sales_total=Decimal("5046.48"),
        orders_count=12,
        orders_promoted=12,
        settled_total=Decimal("5046.48"),
        lines_count=12,
        payout=payout,
    )
    s2 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST2", net_payable=Decimal("3281.81")),
        sales_total=Decimal("3281.81"),
        orders_count=7,
        orders_promoted=7,
        settled_total=Decimal("3281.81"),
        lines_count=7,
        payout=payout,
    )
    rollups = build_payout_rollups("noon", [s1, s2], {"TR1": payout})
    assert len(rollups) == 1
    r = rollups[0]
    assert r.transfer_id == "TR1"
    assert r.statement_ids == ["ST1", "ST2"]
    assert r.statements_count == 2
    assert r.statements_net_total == Decimal("8328.29")
    assert r.transfer_amount == Decimal("8328.29")
    assert r.variance == Decimal("0.00")
    assert r.variance_flag is False
    assert r.flags == []


def test_payout_rollup_flags_a_batch_that_does_not_add_up():
    payout = PayoutInfo("TR1", Decimal("8000.00"), "2026-08-10", "paid")
    s1 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST1", net_payable=Decimal("5046.48")),
        sales_total=Decimal("5046.48"),
        orders_count=1,
        orders_promoted=1,
        settled_total=Decimal("5046.48"),
        lines_count=1,
        payout=payout,
    )
    s2 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST2", net_payable=Decimal("3281.81")),
        sales_total=Decimal("3281.81"),
        orders_count=1,
        orders_promoted=1,
        settled_total=Decimal("3281.81"),
        lines_count=1,
        payout=payout,
    )
    rollups = build_payout_rollups("noon", [s1, s2], {"TR1": payout})
    r = rollups[0]
    assert r.variance == Decimal("-328.29")
    assert r.variance_flag is True
    assert "payout_vs_statements_variance" in r.flags


def test_payout_rollup_null_statement_total_is_unknown_not_variance():
    payout = PayoutInfo("TR1", Decimal("300.00"), "2026-08-10", "paid")
    s1 = build_statement_recon(
        channel="talabat",
        statement=_statement(statement_id="ST1", net_payable=None),
        sales_total=Decimal("300.00"),
        orders_count=1,
        orders_promoted=0,
        settled_total=Decimal("300.00"),
        lines_count=1,
        payout=payout,
    )
    rollups = build_payout_rollups("talabat", [s1], {"TR1": payout})
    r = rollups[0]
    assert r.statements_net_total is None
    assert r.variance is None
    assert r.variance_flag is False
    assert "statement_total_missing" in r.flags


def test_payout_rollup_missing_payout_is_flagged():
    payout = PayoutInfo("TR1", Decimal("100.00"), "2026-08-10", "paid")
    s1 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST1", payout_transfer_id="TR_GONE"),
        sales_total=Decimal("100.00"),
        orders_count=1,
        orders_promoted=1,
        settled_total=Decimal("100.00"),
        lines_count=1,
        payout=None,
    )
    # The statement names TR_GONE but no payout row was fetched for it.
    rollups = build_payout_rollups("noon", [s1], {"TR1": payout})
    assert len(rollups) == 1
    r = rollups[0]
    assert r.transfer_id == "TR_GONE"
    assert r.transfer_amount is None
    assert r.variance is None
    assert "payout_missing" in r.flags


def test_unlinked_statements_produce_no_rollup():
    s1 = build_statement_recon(
        channel="noon",
        statement=_statement(statement_id="ST1", payout_transfer_id=None),
        sales_total=Decimal("100.00"),
        orders_count=1,
        orders_promoted=1,
        settled_total=Decimal("100.00"),
        lines_count=1,
        payout=None,
    )
    assert build_payout_rollups("noon", [s1], {}) == []
