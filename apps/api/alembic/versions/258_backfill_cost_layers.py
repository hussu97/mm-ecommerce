"""Cutover: seed FIFO cost layers for stock that predates the layer ledger.

Before this, `inventory_levels` carried a quantity and a moving `average_cost`
but no cost layers. FIFO consumption needs layers, so this values every
on-hand item as one opening `backfill` layer at its best-known cost:
``average_cost`` when set, else the item's catalogue ``cost``.

Guarded and idempotent: it only touches an (item, warehouse) that has stock, a
positive target cost, and **no active layer** — so it never double-seeds, never
fights a layer the running system already laid down (a purchase after cutover),
and does nothing on a database already migrated. Each seeded layer is anchored to
the item's most recent closed ledger line for provenance; an item whose quantity
has no ledger line at all (pure drift) is left for its first purchase to backfill
at the real price.

Items with no known cost (average 0 and catalogue cost 0) are deliberately left
uncosted here — the first purchase order for them backfills the pre-existing
stock at that PO's unit cost (`cost_layer_service.backfill_uncosted_stock`),
which is the price we actually learn then.

Set-based SQL under a sync bind (no asyncpg array-param typing — 152's lesson).

Revision ID: 258_backfill_cost_layers
Revises: 257_purchasing_rebuild
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "258_backfill_cost_layers"
down_revision: Union[str, None] = "257_purchasing_rebuild"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # One backfill layer per uncosted on-hand (item, warehouse), covering the
    # whole quantity at its best-known cost, anchored to the item's latest closed
    # ledger line. The LATERAL join (not LEFT) drops an item with no closed line,
    # leaving genuinely provenance-less stock for the runtime backfill.
    conn.exec_driver_sql(
        """
        INSERT INTO inventory_cost_layers (
            id, item_id, warehouse_id, branch_id,
            source_transaction_id, source_line_id, purchase_order_id,
            source_kind, posting_sequence, layer_index,
            original_quantity, remaining_quantity, unit_cost,
            received_at, created_at, updated_at
        )
        SELECT
            gen_random_uuid(), lvl.item_id, lvl.warehouse_id, w.branch_id,
            anchor.transaction_id, anchor.line_id, NULL,
            'backfill', anchor.posting_sequence, 0,
            lvl.quantity, lvl.quantity,
            COALESCE(NULLIF(lvl.average_cost, 0), it.cost),
            now(), now(), now()
        FROM inventory_levels lvl
        JOIN warehouses w ON w.id = lvl.warehouse_id
        JOIN inventory_items it ON it.id = lvl.item_id
        JOIN LATERAL (
            SELECT tx.id AS transaction_id, li.id AS line_id, tx.posting_sequence
            FROM inventory_transaction_items li
            JOIN inventory_transactions tx ON tx.id = li.transaction_id
            WHERE li.item_id = lvl.item_id
              AND tx.warehouse_id = lvl.warehouse_id
              AND tx.status = 'closed'
              AND tx.posting_sequence IS NOT NULL
            ORDER BY tx.posting_sequence DESC, li.id DESC
            LIMIT 1
        ) anchor ON true
        WHERE lvl.quantity > 0
          AND COALESCE(NULLIF(lvl.average_cost, 0), it.cost) > 0
          AND NOT EXISTS (
              SELECT 1 FROM inventory_cost_layers cl
              WHERE cl.item_id = lvl.item_id
                AND cl.warehouse_id = lvl.warehouse_id
                AND cl.remaining_quantity > 0
          )
        """
    )

    # Bring the level's cached average onto its catalogue cost where it was blank
    # but the item has a cost — so the value the seeded layer implies is the value
    # the level reports. Levels that already had an average keep it.
    conn.exec_driver_sql(
        """
        UPDATE inventory_levels lvl
        SET average_cost = it.cost
        FROM inventory_items it
        WHERE it.id = lvl.item_id
          AND lvl.quantity > 0
          AND (lvl.average_cost IS NULL OR lvl.average_cost = 0)
          AND it.cost > 0
        """
    )


def downgrade() -> None:
    # Intentionally a no-op: a seeded backfill layer is indistinguishable at the
    # row level from one the running system laid down after cutover (both
    # source_kind='backfill'), so deleting on downgrade could remove real,
    # partly-consumed stock value. The layer ledger is a rebuildable projection —
    # `ledger_service.reconcile_levels(apply=True)` is the tool for correcting it,
    # not this downgrade.
    pass
