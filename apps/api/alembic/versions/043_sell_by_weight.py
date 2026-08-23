"""Weight on an order line, for products priced per kilo.

Revision ID: 043_sell_by_weight
Revises: 042_zones_devices_parking
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "043_sell_by_weight"
down_revision = "042_zones_devices_parking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A separate column rather than a decimal quantity. Quantity stays whole
    # because it means "how many of these", and a 0.４kg line is still one
    # line — the receipt reads "1x Fudge Brownie 0.400 kg", not "0.4x".
    op.add_column("order_items", sa.Column("weight", sa.Numeric(10, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("order_items", "weight")
