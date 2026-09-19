"""Custom-order enquiries: storefront leads that are not bookings.

Adds `custom_order_enquiries`, the table behind the "We cater to" enquiry form on
the home page. A row here is a message a customer sent — name, phone, description,
an optional rough weight, up to four inspiration photos and a wished-for delivery
date. It holds no calendar slot and creates no order; a human reads it and decides
whether it becomes a real `CustomOrder`.

Revision ID: 262_custom_order_enquiries
Revises: 261_production_orders
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "262_custom_order_enquiries"
down_revision: Union[str, None] = "261_production_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_order_enquiries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("customer_name", sa.String(length=150), nullable=False),
        sa.Column("customer_phone", sa.String(length=30), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("approx_kg", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "reference_image_urls",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("delivery_by", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The office triages newest-first; nothing else queries this table.
    op.create_index(
        "ix_custom_order_enquiries_created_at",
        "custom_order_enquiries",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_custom_order_enquiries_created_at",
        table_name="custom_order_enquiries",
    )
    op.drop_table("custom_order_enquiries")
