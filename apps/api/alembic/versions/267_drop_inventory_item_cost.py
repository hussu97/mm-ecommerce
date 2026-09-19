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
    # `cost` carries the ``ck_inventory_item_nonnegative_cost`` CHECK from
    # migration 186. Drop it explicitly first so the up/down is symmetric with the
    # downgrade below (Postgres would cascade it with the column anyway, but being
    # explicit keeps the alembic history and the DB in step).
    op.drop_constraint(
        "ck_inventory_item_nonnegative_cost", "inventory_items", type_="check"
    )
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
    # Restore the constraint migration 186 created, so 186's own downgrade can
    # drop it (the check that failed CI: dropping a constraint that wasn't there).
    op.create_check_constraint(
        "ck_inventory_item_nonnegative_cost", "inventory_items", "cost >= 0"
    )
