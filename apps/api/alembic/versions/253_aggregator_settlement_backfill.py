"""Reconcile the aggregator order feed with the settlement statements already stored.

Three one-time corrections for rows ingested before the settlement back-fill in
`ingest._upsert_statement` existed. Each is a set-based SQL pass, guarded so a
re-run — or a restore of an older dump — matches only the rows still holding the
old value and, once corrected, matches nothing (the same discipline as
`251_noon_commission_incl`). No schema change.

Pass A — Keeta order↔statement link. A Keeta SALES row carries a bill-DETAIL id
(`billId`/`settleId`, e.g. `DT2097…`), a different id namespace from the weekly
statement key (`KEETA_BILL_{shop}_{cycleEnd}`). Ingest stamped that detail id onto
`aggregator_order.statement_id`, so every Keeta order pointed at a statement that
does not exist and the settlement reconciler's order↔statement join found nothing.
Null the ids that match no real statement, then relink from the bill's own
per-order lines (`external_order_id`). The provider now leaves it unset at sales
time, so this only ever fixes the backlog. Orders whose bill has not been scraped
yet keep a null link (correct — they have no published statement).

Pass B — settlement-authoritative order net. The hourly sales pull stamps a
PROVISIONAL `net_payable`, and for an unsettled order that provision can be wrong,
not just absent: a noon OMS order carries `net_payable == gross_sales` (no cut
taken) until its weekly settlement publishes, then the 10-day lookback stops
re-pulling it, so it over-states its payout by the whole commission; Deliveroo's
sales feed carries no net at all. Roll the published statement's own per-order net
(SIGNED) onto the order — it WINS over the provision — and fill `gross_sales` only
where absent. Fee columns are deliberately untouched (see the back-fill docstring:
the Fees roll-up reads the statement lines directly, and noon's order commission is
VAT-inclusive while its line is ex-VAT). Matches Deliveroo via its `drn_id`, the
same fallback the ingest join uses.

Pass C — statement header totals. Deliveroo/Keeta/Careem publish only lines, so
`gross_sales`/`net_payable`/`total_fees`/`total_vat` on the header land null and
every header-level read goes dark. Derive them from the lines — gross/fees/VAT as
magnitudes, net SIGNED — filling only a total the provider left null (noon and
Talabat declare their own).

Revision ID: 253_aggregator_settlement_backfill
Revises: 252_polygon_branch_priority_seed
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "253_aggregator_settlement_backfill"
down_revision: Union[str, None] = "252_polygon_branch_priority_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The category vocabulary mirrored from `services/aggregators/statement_categories`
# (the runtime source of truth). Kept in sync by eye; this is a one-shot backfill,
# the live path imports the Python predicates.
_IS_GROSS = (
    "lower(coalesce(fee_category,''))='gross_sales' "
    "OR lower(coalesce(line_type,'')) IN ('gross_sales','sales','sale')"
)
_IS_NET = (
    "lower(coalesce(fee_category,''))='net_payable' "
    "OR lower(coalesce(line_type,'')) IN ('net_payable','payout','settlement')"
)
_IS_VAT = "lower(coalesce(line_type,''))='vat' OR lower(coalesce(fee_category,'')) LIKE '%vat%'"
_IS_OTHER_REVENUE = (
    "lower(coalesce(fee_category,'')) IN ('merchant_compensation','adjustment_increase') "
    "OR (lower(coalesce(line_type,''))='adjustment' AND amount > 0)"
)
_IS_REFUND = (
    "lower(coalesce(fee_category,'')) IN ('customer_refund','merchant_liability')"
)
# A fee is the residual: not gross, not net, not VAT, not inbound revenue, not a refund.
_IS_FEE = f"NOT (({_IS_GROSS}) OR ({_IS_NET}) OR ({_IS_VAT}) OR ({_IS_OTHER_REVENUE}) OR ({_IS_REFUND}))"

# Order↔line match, mirroring `_order_matches_line_ids` (Deliveroo's `drn_id`).
_ORDER_MATCHES_LINE = (
    "o.external_order_id = agg.external_order_id "
    "OR o.display_ref = agg.external_order_id "
    "OR (o.raw->'detail'->>'drn_id') = agg.external_order_id"
)


def upgrade() -> None:
    # ── Pass A — Keeta order↔statement link ──────────────────────────────────
    op.execute(
        """
        UPDATE aggregator_order o
        SET statement_id = NULL
        WHERE o.channel = 'keeta'
          AND o.statement_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM aggregator_statement s
              WHERE s.channel = 'keeta' AND s.statement_id = o.statement_id
          )
        """
    )
    op.execute(
        """
        UPDATE aggregator_order o
        SET statement_id = sc.statement_id
        FROM (
            SELECT DISTINCT external_order_id, statement_id
            FROM aggregator_statement_line
            WHERE channel = 'keeta'
              AND statement_id IS NOT NULL
              AND external_order_id IS NOT NULL
        ) sc
        WHERE o.channel = 'keeta'
          AND o.statement_id IS NULL
          AND o.external_order_id = sc.external_order_id
        """
    )

    # ── Pass B — settlement-authoritative order net (+ gross fill-null) ───────
    op.execute(
        f"""
        WITH agg AS (
            SELECT channel, external_order_id,
                sum(amount) FILTER (WHERE {_IS_NET}) AS net,
                sum(abs(amount)) FILTER (WHERE {_IS_GROSS}) AS gross
            FROM aggregator_statement_line
            WHERE grain = 'order' AND external_order_id IS NOT NULL
            GROUP BY channel, external_order_id
        )
        UPDATE aggregator_order o
        SET net_payable = COALESCE(agg.net, o.net_payable),
            gross_sales = COALESCE(o.gross_sales, agg.gross)
        FROM agg
        WHERE o.channel = agg.channel
          AND ({_ORDER_MATCHES_LINE})
          AND (
              (agg.net IS NOT NULL AND o.net_payable IS DISTINCT FROM agg.net)
              OR (o.gross_sales IS NULL AND agg.gross IS NOT NULL)
          )
        """
    )

    # ── Pass C — statement header totals from lines ──────────────────────────
    op.execute(
        f"""
        WITH tot AS (
            SELECT channel, statement_id,
                sum(abs(amount)) FILTER (WHERE {_IS_GROSS}) AS gross,
                sum(amount) FILTER (WHERE {_IS_NET}) AS net,
                sum(abs(amount)) FILTER (WHERE {_IS_FEE}) AS fees,
                sum(abs(amount)) FILTER (WHERE {_IS_VAT}) AS vat
            FROM aggregator_statement_line
            WHERE statement_id IS NOT NULL
            GROUP BY channel, statement_id
        )
        UPDATE aggregator_statement s
        SET gross_sales = COALESCE(s.gross_sales, tot.gross),
            net_payable = COALESCE(s.net_payable, tot.net),
            total_fees  = COALESCE(s.total_fees, tot.fees),
            total_vat   = COALESCE(s.total_vat, tot.vat)
        FROM tot
        WHERE s.channel = tot.channel
          AND s.statement_id = tot.statement_id
          AND (
              (s.gross_sales IS NULL AND tot.gross IS NOT NULL)
              OR (s.net_payable IS NULL AND tot.net IS NOT NULL)
              OR (s.total_fees IS NULL AND tot.fees IS NOT NULL)
              OR (s.total_vat IS NULL AND tot.vat IS NOT NULL)
          )
        """
    )


def downgrade() -> None:
    # A data reconciliation, not a schema change — the corrected settlement figures
    # are the true ones, so there is nothing to reverse.
    pass
