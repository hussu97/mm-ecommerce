"""Add performance indexes for common queries

Revision ID: 012_performance_indexes
Revises: 011_cms_home_page
Create Date: 2026-03-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "012_performance_indexes"
down_revision: Union[str, None] = "011_cms_home_page"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_orders_created_at", "orders", ["created_at"])
    op.create_index("ix_users_is_guest", "users", ["is_guest"])
    op.create_index("ix_users_is_admin", "users", ["is_admin"])
    op.create_index("ix_products_is_active", "products", ["is_active"])
    op.create_index("ix_products_is_featured", "products", ["is_featured"])


def downgrade() -> None:
    op.drop_index("ix_products_is_featured", table_name="products")
    op.drop_index("ix_products_is_active", table_name="products")
    op.drop_index("ix_users_is_admin", table_name="users")
    op.drop_index("ix_users_is_guest", table_name="users")
    op.drop_index("ix_orders_created_at", table_name="orders")
