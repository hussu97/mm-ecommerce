"""Remove price tags, reservations, spot checks and clock-in shifts.

Four more features built against the Foodics parity matrix and never used by
this business.

* **Price tags** — alternative price lists. Never had a router at all, so there
  has never been a way to create one, let alone trade on it.
* **Reservations** — table booking. There are no tables to book; this is a
  bakery with a counter.
* **Spot checks** — mid-shift cash counts. The endpoint and the `pos.spot_check`
  permission both exist and no client has ever called it.
* **Shifts** — clock-in/clock-out. Staff hours are not tracked here; the till
  open/close pair is what a shift actually means in this shop, and that stays.

No table outside these references any of them, so they drop independently.

`orders.table_id` and the POS floor-plan tables are deliberately *not* touched
here — they are a larger change that reaches the sales-by-table report
dimensions and the register, and belongs in its own migration.

Revision ID: 080_drop_unbuilt_pos
Revises: 079_drop_unused_marketing
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "080_drop_unbuilt_pos"
down_revision: Union[str, None] = "079_drop_unused_marketing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = sa.dialects.postgresql.UUID


def upgrade() -> None:
    op.drop_table("spot_check_items")
    op.drop_table("spot_checks")
    op.drop_table("reservations")
    op.drop_table("product_prices")
    op.drop_table("price_tags")
    op.drop_table("shifts")


def downgrade() -> None:
    op.create_table(
        "price_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column(
            "translations",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("reference", sa.String(50), nullable=True, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "product_prices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "price_tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("price_tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id", "price_tag_id", name="uq_product_price_tag"),
    )
    op.create_table(
        "reservations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("table_id", UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("customer_phone", sa.String(30), nullable=True),
        sa.Column("guests", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duration_minutes", sa.Integer(), nullable=False, server_default="90"
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "spot_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(40), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("business_date", sa.String(10), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "spot_check_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "spot_check_id",
            UUID(as_uuid=True),
            sa.ForeignKey("spot_checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("inventory_item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expected_quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("counted_quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("variance", sa.Numeric(14, 4), nullable=False),
    )
    op.create_table(
        "shifts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("clocked_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clocked_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
