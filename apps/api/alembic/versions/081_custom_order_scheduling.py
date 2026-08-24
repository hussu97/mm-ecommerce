"""Custom cakes get a diary.

A stock brownie box is made in bulk and sold until it runs out. A custom cake is
a day of somebody's work, and only so many can be promised for the same date.
Nothing in the schema knew that, so the shop's only protection against taking
four wedding cakes for one Saturday was somebody remembering.

Three parts:

* `products.is_customisable` turns the extra flow on for a product, and
  `products.lead_time_days` says how much notice it needs. That is a different
  question from `preparation_time`, which is minutes in the kitchen — a cake can
  take two hours to make and still need three days' notice because the diary is
  full.
* `business_settings` gains the capacity itself. One per day, because that is
  what this kitchen can make alongside normal production, and configurable
  because the honest answer changes with staff.
* `custom_orders` is the diary. It carries a `due_date` and a customer rather
  than pointing at an order, because most of this bakery's custom work arrives
  as an Instagram or WhatsApp message and never becomes a website order at all.
  A calendar that only knew about website orders would show a free Saturday that
  a DM had already taken — worse than no calendar, because somebody would trust
  it.

`custom_order_blackouts` is a date that takes nothing whatever the capacity says
— Eid, a holiday, the week the oven is serviced. Separate from setting capacity
to zero because it carries a reason and because capacity is a standing number
rather than a diary.

Revision ID: 081_custom_order_scheduling
Revises: 080_drop_unbuilt_pos
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "081_custom_order_scheduling"
down_revision: Union[str, None] = "080_drop_unbuilt_pos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = sa.dialects.postgresql.UUID


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_customisable",
            sa.Boolean(),
            nullable=False,
            server_default="false",
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
            sa.dialects.postgresql.ARRAY(sa.String()),
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
        sa.Column(
            "brief",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
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
    # The calendar's only real query: what is booked on this date.
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


def downgrade() -> None:
    op.drop_table("custom_order_blackouts")
    op.drop_table("custom_orders")
    op.drop_column("business_settings", "custom_order_max_days_ahead")
    op.drop_column("business_settings", "custom_order_lead_days")
    op.drop_column("business_settings", "custom_orders_per_day")
    op.drop_column("products", "lead_time_days")
    op.drop_column("products", "is_customisable")
