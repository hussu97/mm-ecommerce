"""Drop the redundant product cost columns — product cost is the recipe now.

``products.cost`` was written only by the CSV product importer and read only by
the CSV exporter; it was never exposed by the API, shown in the console, or used
by any margin/COGS calculation, so it went stale the moment an ingredient price
moved. ``products.costing_method`` was entirely dead (never read or written by
application code).

A product's cost is now derived live from its active recipe's ingredient FIFO
cost (``recipe_service.product_recipe_unit_cost``), exactly as its inventory-item
cousins were after migration 267. The CSV export emits that live figure and the
importer ignores any ``cost`` column, so an older export still loads.

Revision ID: 274_drop_product_cost
Revises: 273_po_status_voided
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "274_drop_product_cost"
down_revision: Union[str, None] = "273_po_status_voided"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Neither column carries a CHECK constraint (unlike inventory_items.cost in
    # migration 186), so a plain drop is symmetric with the downgrade below.
    op.drop_column("products", "cost")
    op.drop_column("products", "costing_method")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "costing_method",
            sa.String(length=20),
            nullable=False,
            server_default="fixed",
        ),
    )
    op.add_column(
        "products",
        sa.Column("cost", sa.Numeric(10, 2), nullable=True),
    )
