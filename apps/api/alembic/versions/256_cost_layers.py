"""FIFO cost layers and the consumption (COGS) trail.

Inventory costing moves from moving weighted-average to **FIFO**. Every inbound
movement lays down an `inventory_cost_layers` row (a quantity at a unit cost);
every issue consumes the oldest layers first, recording an
`inventory_cost_layer_consumptions` row per layer it draws from.
``inventory_levels.average_cost`` becomes the surviving layers' weighted average
(Σ remaining × cost ÷ Σ remaining), computed by the posting path — so every
existing reader keeps working while COGS becomes true FIFO.

Both tables are a **projection** of the immutable ledger
(`inventory_transaction_items`), rebuildable by
`ledger_service.reconcile_levels(apply=True)`, so neither carries an
immutability trigger. No new sequence: layers copy ``posting_sequence`` from the
transaction that created them, which is the FIFO ordering key.

Revision ID: 256_cost_layers
Revises: 255_bike_noon_live_pair
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "256_cost_layers"
down_revision: Union[str, None] = "255_bike_noon_live_pair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SOURCE_KINDS = (
    "purchasing",
    "opening_balance",
    "backfill",
    "transfer_receive",
    "production",
    "return_from_orders",
    "positive_adjustment",
    "count_overage",
)


def upgrade() -> None:
    # Links a reversal's line to the line it undoes, so the FIFO engine can
    # restore/remove exactly the layers the original movement touched.
    op.add_column(
        "inventory_transaction_items",
        sa.Column(
            "reverses_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_transaction_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )

    allowed = ", ".join(f"'{value}'" for value in _SOURCE_KINDS)
    op.create_table(
        "inventory_cost_layers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_transaction_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("posting_sequence", sa.BigInteger(), nullable=False),
        sa.Column("layer_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("original_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_cost", sa.Numeric(16, 6), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"source_kind IN ({allowed})",
            name="ck_inventory_cost_layers_source_kind_allowed",
        ),
        sa.CheckConstraint(
            "remaining_quantity >= 0", name="ck_inventory_cost_layer_remaining"
        ),
        sa.CheckConstraint(
            "original_quantity >= 0", name="ck_inventory_cost_layer_original"
        ),
        sa.CheckConstraint("unit_cost >= 0", name="ck_inventory_cost_layer_cost"),
    )
    op.create_index(
        "ix_inventory_cost_layers_item_id", "inventory_cost_layers", ["item_id"]
    )
    op.create_index(
        "ix_inventory_cost_layers_warehouse_id",
        "inventory_cost_layers",
        ["warehouse_id"],
    )
    op.create_index(
        "ix_inventory_cost_layers_branch_id",
        "inventory_cost_layers",
        ["branch_id"],
    )
    op.create_index(
        "ix_inventory_cost_layers_source_transaction_id",
        "inventory_cost_layers",
        ["source_transaction_id"],
    )
    # Reversing a receipt looks up its layers by the line that created them.
    op.create_index(
        "ix_inventory_cost_layers_source_line_id",
        "inventory_cost_layers",
        ["source_line_id"],
    )
    op.create_index(
        "ix_inventory_cost_layers_source_kind",
        "inventory_cost_layers",
        ["source_kind"],
    )
    # The FIFO consumption path reads only active layers, oldest first.
    op.create_index(
        "ix_inventory_cost_layers_fifo",
        "inventory_cost_layers",
        ["item_id", "warehouse_id", "posting_sequence", "layer_index"],
        postgresql_where=sa.text("remaining_quantity > 0"),
    )

    op.create_table(
        "inventory_cost_layer_consumptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "consuming_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_transaction_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "layer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_cost_layers.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_cost", sa.Numeric(16, 6), nullable=False),
        sa.Column("total_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("posting_sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "is_shortfall",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "quantity >= 0", name="ck_inventory_cost_consumption_quantity"
        ),
    )
    op.create_index(
        "ix_inventory_cost_layer_consumptions_line",
        "inventory_cost_layer_consumptions",
        ["consuming_line_id"],
    )
    op.create_index(
        "ix_inventory_cost_layer_consumptions_layer",
        "inventory_cost_layer_consumptions",
        ["layer_id"],
    )
    op.create_index(
        "ix_inventory_cost_layer_consumptions_item",
        "inventory_cost_layer_consumptions",
        ["item_id"],
    )


def downgrade() -> None:
    op.drop_table("inventory_cost_layer_consumptions")
    op.drop_table("inventory_cost_layers")
    op.drop_column("inventory_transaction_items", "reverses_line_id")
