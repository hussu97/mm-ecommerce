"""Separate the storefront catalogue from the POS catalogue.

Revision ID: 040_product_web_visibility
Revises: 039_operations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "040_product_web_visibility"
down_revision = "039_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing products are all website products, so they default to visible;
    # only the POS import opts new items out.
    op.add_column(
        "products",
        sa.Column(
            "is_web_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # The storefront filters on this on every listing request.
    op.create_index(
        "ix_products_is_web_visible", "products", ["is_web_visible"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_products_is_web_visible", table_name="products")
    op.drop_column("products", "is_web_visible")
