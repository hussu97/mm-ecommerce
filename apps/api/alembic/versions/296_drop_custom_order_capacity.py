"""Drop the custom-order capacity calendar (the contract half of 294).

Custom orders v2 (294/295) replaced the capacity diary with ``orders`` rows of
``source = 'custom'``. 294 left the old schema in place because the container
still serving during that cutover mapped it. Nothing maps it now, so it goes:

- the ``custom_orders`` and ``custom_order_blackouts`` tables (empty on prod,
  and nothing references them);
- ``business_settings.custom_orders_per_day``, ``custom_order_lead_days`` and
  ``custom_order_max_days_ahead``;
- ``products.is_customisable`` and ``lead_time_days`` (no product sets either).

``downgrade`` puts the empty tables and the columns back with 081's shape and
defaults; the rows were never there to restore.

Revision ID: 296_drop_custom_order_capacity
Revises: 295_custom_orders_setup
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision: str = "296_drop_custom_order_capacity"
down_revision: Union[str, None] = "295_custom_orders_setup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("custom_order_blackouts")
    op.drop_table("custom_orders")
    op.drop_column("business_settings", "custom_order_max_days_ahead")
    op.drop_column("business_settings", "custom_order_lead_days")
    op.drop_column("business_settings", "custom_orders_per_day")
    op.drop_column("products", "lead_time_days")
    op.drop_column("products", "is_customisable")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_customisable", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column("products", sa.Column("lead_time_days", sa.Integer(), nullable=True))
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_orders_per_day", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_order_lead_days", sa.Integer(), nullable=False, server_default="3"
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_order_max_days_ahead",
            sa.Integer(),
            nullable=False,
            server_default="180",
        ),
    )

    op.create_table(
        "custom_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="enquiry"),
        sa.Column("source", sa.String(20), nullable=False, server_default="website"),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("customer_phone", sa.String(30), nullable=True),
        sa.Column("customer_email", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cake_message", sa.String(200), nullable=True),
        sa.Column("flavour", sa.String(120), nullable=True),
        sa.Column("size_label", sa.String(60), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=True),
        sa.Column(
            "reference_image_urls",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("quoted_total", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "deposit_amount", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("deposit_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("brief", JSONB(), nullable=False, server_default="{}"),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_custom_orders_due_date", "custom_orders", ["due_date"])
    op.create_index("ix_custom_orders_status", "custom_orders", ["status"])
    op.create_index("ix_custom_orders_order_id", "custom_orders", ["order_id"])
    op.create_index("ix_custom_orders_branch_id", "custom_orders", ["branch_id"])
    op.create_index(
        "ix_custom_orders_due_date_status", "custom_orders", ["due_date", "status"]
    )

    op.create_table(
        "custom_order_blackouts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("blackout_date", sa.Date(), nullable=False, unique=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_custom_order_blackouts_blackout_date",
        "custom_order_blackouts",
        ["blackout_date"],
    )
