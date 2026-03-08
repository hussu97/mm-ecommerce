"""Add missing indexes for cart_items, products, and orders

Revision ID: 017_cart_and_order_indexes
Revises: 016_fix_cat_brownies_slug
Create Date: 2026-03-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "017_cart_and_order_indexes"
down_revision: Union[str, None] = "016_fix_cat_brownies_slug"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # FK with no index — used in cart merge lookups
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cart_items_product_id ON cart_items (product_id)"
    )

    # Featured products: WHERE is_active=T AND is_featured=T ORDER BY display_order
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_active_featured_order"
        " ON products (is_active, is_featured, display_order)"
    )

    # Admin order list: filter user_id, sort by created_at DESC
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_user_created ON orders (user_id, created_at DESC)"
    )

    # Admin order list: filter status, sort by created_at DESC
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_status_created ON orders (status, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_orders_status_created")
    op.execute("DROP INDEX IF EXISTS ix_orders_user_created")
    op.execute("DROP INDEX IF EXISTS ix_products_active_featured_order")
    op.execute("DROP INDEX IF EXISTS ix_cart_items_product_id")
