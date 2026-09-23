"""FIFO costing v3: provisional cost, per-line projected cost, engine state.

The v3 engine (``app/services/inventory/costing_engine.py``) replays the
immutable ledger into the cost projection. This adds what it writes:

* ``cost_is_provisional`` / ``priced_by_line_id`` on layers and consumptions —
  whether a cost is still an estimate, and which ledger line priced it.
* ``inventory_line_costs`` — every closed line's *current* cost, beside the
  immutable booked one, for the costing history and COGS.
* ``inventory_costing_state`` — the singleton holding the cutover sequence
  (pre-v3 cost adjustments were workarounds and are skipped by the replay).
* ``inventory_costing_dirty`` — warehouses waiting on an estate replay. Every
  warehouse is marked here, so the first scheduler tick after this deploy
  restates the whole estate.

Projection tables only — nothing here touches the immutable ledger.

Revision ID: 281_fifo_costing_v3
Revises: 280_delivery_area_zone_cache
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "281_fifo_costing_v3"
down_revision: Union[str, None] = "280_delivery_area_zone_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("inventory_cost_layers", "inventory_cost_layer_consumptions"):
        op.add_column(
            table,
            sa.Column(
                "cost_is_provisional",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "priced_by_line_id",
                sa.UUID(),
                sa.ForeignKey("inventory_transaction_items.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    op.create_index(
        "ix_inventory_cost_layer_consumptions_item_warehouse",
        "inventory_cost_layer_consumptions",
        ["item_id", "warehouse_id"],
    )

    op.create_table(
        "inventory_line_costs",
        sa.Column(
            "line_id",
            sa.UUID(),
            sa.ForeignKey("inventory_transaction_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "transaction_id",
            sa.UUID(),
            sa.ForeignKey("inventory_transactions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.UUID(),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.UUID(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            sa.UUID(),
            sa.ForeignKey("branches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("posting_sequence", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 10), nullable=False),
        sa.Column("total_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("booked_unit_cost", sa.Numeric(20, 10), nullable=False),
        sa.Column(
            "is_provisional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "priced_by_line_id",
            sa.UUID(),
            sa.ForeignKey("inventory_transaction_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("running_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("running_value", sa.Numeric(20, 4), nullable=False),
        sa.Column(
            "superseded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index(
        "ix_inventory_line_costs_item_history",
        "inventory_line_costs",
        ["item_id", "warehouse_id", "posting_sequence"],
    )
    op.create_index(
        "ix_inventory_line_costs_warehouse_id",
        "inventory_line_costs",
        ["warehouse_id"],
    )
    op.create_index(
        "ix_inventory_line_costs_transaction_id",
        "inventory_line_costs",
        ["transaction_id"],
    )

    op.create_table(
        "inventory_costing_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column("cutover_sequence", sa.BigInteger(), nullable=True),
        sa.Column("last_estate_replay_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_estate_replay_ms", sa.Integer(), nullable=True),
        sa.Column("last_estate_changes", sa.Integer(), nullable=True),
        sa.CheckConstraint("id", name="ck_inventory_costing_state_singleton"),
    )
    # Every cost adjustment posted so far patched a bug v3 fixes (the recost
    # sweep, one-off recosts, count-overage revaluations), so the cutover is the
    # ledger's high-water mark as of this deploy.
    op.execute(
        """
        INSERT INTO inventory_costing_state (id, cutover_sequence)
        SELECT true, COALESCE(MAX(posting_sequence), 0)
        FROM inventory_transactions
        WHERE status = 'closed'
        """
    )

    op.create_table(
        "inventory_costing_dirty",
        sa.Column(
            "warehouse_id",
            sa.UUID(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "dirty_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Mark every warehouse, so the first estate sweep after this deploy restates
    # the whole estate under the v3 rules.
    op.execute(
        """
        INSERT INTO inventory_costing_dirty (warehouse_id)
        SELECT id FROM warehouses
        """
    )


def downgrade() -> None:
    op.drop_table("inventory_costing_dirty")
    op.drop_table("inventory_costing_state")
    op.drop_index(
        "ix_inventory_line_costs_transaction_id", table_name="inventory_line_costs"
    )
    op.drop_index(
        "ix_inventory_line_costs_warehouse_id", table_name="inventory_line_costs"
    )
    op.drop_index(
        "ix_inventory_line_costs_item_history", table_name="inventory_line_costs"
    )
    op.drop_table("inventory_line_costs")
    op.drop_index(
        "ix_inventory_cost_layer_consumptions_item_warehouse",
        table_name="inventory_cost_layer_consumptions",
    )
    for table in ("inventory_cost_layers", "inventory_cost_layer_consumptions"):
        op.drop_column(table, "priced_by_line_id")
        op.drop_column(table, "cost_is_provisional")
