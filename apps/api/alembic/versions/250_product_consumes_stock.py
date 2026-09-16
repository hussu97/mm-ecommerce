"""Add products.consumes_stock — a product that draws no tracked inventory.

Inventory-v2 expects every sold product to carry a recipe: an order line for a
product with no active recipe raises ``missing_recipe`` in
``recipe_service.snapshot_order`` and its source event sits PENDING, re-snapshot
every sweep, waiting for a recipe that (for a made-to-order beverage MM does not
cost at the ingredient level, or an item whose consumption lives entirely on its
modifier options) never comes. There was no way to say "this product genuinely
consumes nothing" short of fabricating an empty recipe.

This adds the signal. ``consumes_stock`` defaults true, so every existing product
keeps today's behaviour; setting it false makes ``snapshot_order`` skip the
product's recipe expansion (no warning) and lets the event close as a clean
no-movement. Distinct from ``is_stock_product`` (the legacy per-product
``stock_quantity`` counter, untouched here).

Revision ID: 250_product_consumes_stock
Revises: 249_source_snapshot_remutable
Create Date: 2026-09-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "250_product_consumes_stock"
down_revision: Union[str, None] = "249_source_snapshot_remutable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "consumes_stock",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "consumes_stock")
