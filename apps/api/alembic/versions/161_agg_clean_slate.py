"""Reset the scraped aggregator data to a clean slate, and index the recon joins.

The aggregator ingestion was reworked end to end — richer parsing (items,
modifiers, customer, numeric-status decode), a real Keeta finance path, per-order
statement lines, payout↔statement coupling, a wider promotion window and GCS
invoice archival. Rather than carry rows written by the old, gap-ridden code
through a fragile backfill, this migration TRUNCATEs the SCRAPED aggregator
tables so the next ingest refills them cleanly under the new code. Everything is
re-fetchable from the marketplaces, and every write is an idempotent upsert on a
channel-scoped natural key, so a clean slate loses nothing that cannot be pulled
again on the next daily pass.

**What is wiped** (scraped, re-fetchable): `aggregator_order`,
`aggregator_order_item`, `aggregator_statement`, `aggregator_statement_line`,
`aggregator_payout`, `aggregator_reconciliation`, `aggregator_sync_run`.

**What is KEPT** (config / credentials / cross-system, not re-fetchable):
`aggregator_account` (login recipes + sealed secrets), `aggregator_session`
(the captured sessions — losing these would force a headed re-login of every
channel), `aggregator_branch_map` (outlet→branch mapping). And nothing outside
the aggregator scrape is touched: Foodics (`foodics_branch_map`) and GrubOps
(`grubops_location_map`, `grubops_order_map`, `grubops_sync_state`) and the
`orders` table stay exactly as they are — the promoted MM orders re-converge onto
their existing rows through the `(source, external_reference)` key on the next
promotion pass, so this does not orphan or duplicate them.

Also adds two indexes the reconciliation/promotion joins were missing:
`grubops_order_map(source_channel, external_id)` (the per-order lookup
`reconcile._find_mm_order` runs) and `aggregator_sync_run(started_at)` (the
`_slot_ran_since` catch-up scan).

Revision ID: 161_agg_clean_slate
Revises: 160_agg_stmt_invoice
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "161_agg_clean_slate"
down_revision: Union[str, None] = "160_agg_stmt_invoice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The scraped tables, in one TRUNCATE so FK order does not matter. CASCADE only
#: reaches rows inside this set (order_item / reconciliation FK back to
#: aggregator_order, both listed); nothing outside references these, so it cannot
#: reach `orders`, GrubOps or Foodics. RESTART IDENTITY is a no-op here (UUID PKs)
#: but keeps the intent explicit.
_SCRAPED_TABLES = (
    "aggregator_reconciliation",
    "aggregator_statement_line",
    "aggregator_order_item",
    "aggregator_payout",
    "aggregator_statement",
    "aggregator_order",
    "aggregator_sync_run",
)


def upgrade() -> None:
    op.execute(
        "TRUNCATE TABLE " + ", ".join(_SCRAPED_TABLES) + " RESTART IDENTITY CASCADE"
    )
    # The payments leg of the reconciliation chain: which payout settled this
    # statement. One payout batches every statement due since the last transfer,
    # so the link lives on the statement (many statements → one transfer id), not
    # on the payout. Resolved by `ingest.link_statements_to_payouts`.
    # `ADD COLUMN IF NOT EXISTS` for parity with the guarded indexes below — so a
    # partially-applied or hand-patched prod (column already present) re-runs clean
    # rather than aborting the whole upgrade.
    op.execute(
        "ALTER TABLE aggregator_statement "
        "ADD COLUMN IF NOT EXISTS payout_transfer_id VARCHAR(64)"
    )
    op.create_index(
        "ix_aggregator_statement_payout",
        "aggregator_statement",
        ["channel", "payout_transfer_id"],
        if_not_exists=True,
    )
    # The reconcile join: grubops_order_map(source_channel, external_id) was only
    # indexed on its own id / mm_order_id, so `_find_mm_order` seq-scanned it once
    # per aggregator order.
    op.create_index(
        "ix_grubops_order_map_source_external",
        "grubops_order_map",
        ["source_channel", "external_id"],
        if_not_exists=True,
    )
    # The scheduler's boot catch-up filters sync runs by start time.
    op.create_index(
        "ix_aggregator_sync_run_started_at",
        "aggregator_sync_run",
        ["started_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    # The truncated rows cannot be restored — they are re-fetched by the ingest,
    # not un-deleted. Downgrade only drops what this migration added structurally.
    op.drop_index(
        "ix_aggregator_statement_payout",
        table_name="aggregator_statement",
        if_exists=True,
    )
    op.drop_column("aggregator_statement", "payout_transfer_id")
    op.drop_index(
        "ix_aggregator_sync_run_started_at",
        table_name="aggregator_sync_run",
        if_exists=True,
    )
    op.drop_index(
        "ix_grubops_order_map_source_external",
        table_name="grubops_order_map",
        if_exists=True,
    )
