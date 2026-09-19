"""Drop the redundant per-item cost columns — cost is FIFO now.

``inventory_items.cost`` and ``inventory_items.costing_method`` were a second,
never-updated cost workflow: receiving wrote the true cost into FIFO cost layers
and ``inventory_levels.average_cost``, but ``cost`` kept whatever a CSV import
last set, so item cost on screen went stale the moment stock was received.

An item's cost is now derived from its surviving cost layers
(``cost_layer_service.item_average_cost`` / ``InventoryLevel.average_cost``) — 0
until its first receipt or production. These columns have no readers left.

Revision ID: 267_drop_inventory_item_cost
Revises: 266_po_receiving_variance
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "267_drop_inventory_item_cost"
down_revision: Union[str, None] = "266_po_receiving_variance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("inventory_items", "cost")
    op.drop_column("inventory_items", "costing_method")


def downgrade() -> None:
    op.add_column(
        "inventory_items",
        sa.Column(
            "costing_method",
            sa.String(length=30),
            nullable=False,
            server_default="fixed",
        ),
    )
    op.add_column(
        "inventory_items",
        sa.Column(
            "cost",
            sa.Numeric(16, 6),
            nullable=False,
            server_default="0",
        ),
    )
