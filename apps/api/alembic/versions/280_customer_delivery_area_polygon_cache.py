"""Cache current delivery-zone membership for customer address points.

Revision ID: 280_delivery_area_zone_cache
Revises: 277_customer_delivery_area_cache
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "280_delivery_area_zone_cache"
down_revision: Union[str, None] = "277_customer_delivery_area_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_delivery_area_polygon_cache_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column(
            "dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.execute(
        "INSERT INTO customer_delivery_area_polygon_cache_state (id, dirty) VALUES (true, true)"
    )
    op.create_table(
        "customer_delivery_area_polygon_cache",
        sa.Column(
            "order_id",
            sa.UUID(),
            sa.ForeignKey("customer_delivery_area_cache.order_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "polygon_id",
            sa.UUID(),
            sa.ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_customer_delivery_area_polygon_cache_polygon_id",
        "customer_delivery_area_polygon_cache",
        ["polygon_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_delivery_area_polygon_cache_polygon_id",
        table_name="customer_delivery_area_polygon_cache",
    )
    op.drop_table("customer_delivery_area_polygon_cache")
    op.drop_table("customer_delivery_area_polygon_cache_state")
