"""Add stock_quantity column to products table

stock_quantity was previously on product_variants (dropped in 003).
cart_service.merge() references product.stock_quantity for stock-capped
cart merges, causing an AttributeError crash on every cart merge request.

Revision ID: 026
Revises: 025
Create Date: 2026-04-14
"""

import sqlalchemy as sa
from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "stock_quantity",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "stock_quantity")
