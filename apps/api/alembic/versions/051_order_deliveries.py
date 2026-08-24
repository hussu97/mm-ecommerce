"""Record how an order actually got delivered, and what it cost.

Until now the order table stopped at `packed`. Everything after that — who
collected it, whether it arrived, what the courier charged — happened in a
WhatsApp thread. That was survivable while dispatch was a person; it is not
survivable once a courier is booked by the API and reports back over webhooks.

Three changes:

  * `order_deliveries` — one row per delivery order, integrated or not, holding
    the zone that priced it, the estimate taken at checkout, the courier
    booking, the driver, proof of delivery and the timeline. Third-party orders
    get a row too, otherwise a cost report would silently cover only the zones
    we automated.

  * `orderstatusenum` gains `out_for_delivery` and `delivered`, so the parcel
    leaving the kitchen and the parcel arriving are distinguishable states
    rather than a note in the admin field.

  * `carts` gains the courier estimate for the pin currently dropped. It is
    never shown and never charged; it is kept so the fee table can be argued
    with using real numbers, including from baskets that never converted.

Revision ID: 051_order_deliveries
Revises: 050_delivery_zone_fulfilment
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "051_order_deliveries"
down_revision: Union[str, None] = "050_delivery_zone_fulfilment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'out_for_delivery'")
    op.execute("ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'delivered'")

    op.create_table(
        "order_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("zone_name", sa.String(length=100), nullable=True),
        sa.Column("fee_charged", sa.Numeric(10, 2), nullable=True),
        sa.Column("quoted_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("quoted_currency", sa.String(length=3), nullable=True),
        sa.Column("quoted_distance_m", sa.Integer(), nullable=True),
        sa.Column("quotation_id", sa.String(length=64), nullable=True),
        sa.Column("quoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("courier_order_id", sa.String(length=64), nullable=True),
        sa.Column(
            "previous_courier_order_ids", JSONB, nullable=False, server_default="[]"
        ),
        sa.Column("courier_status", sa.String(length=30), nullable=True),
        sa.Column("courier_previous_status", sa.String(length=30), nullable=True),
        sa.Column("share_link", sa.Text(), nullable=True),
        sa.Column("cost_total", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_breakdown", JSONB, nullable=True),
        sa.Column("driver_id", sa.String(length=64), nullable=True),
        sa.Column("driver_name", sa.String(length=150), nullable=True),
        sa.Column("driver_phone", sa.String(length=30), nullable=True),
        sa.Column("driver_plate", sa.String(length=30), nullable=True),
        sa.Column("driver_latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("driver_longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("pod_status", sa.String(length=20), nullable=True),
        sa.Column("pod_image_url", sa.Text(), nullable=True),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_party", sa.String(length=20), nullable=True),
        sa.Column("cancel_reason", sa.String(length=80), nullable=True),
        sa.Column("last_payload", JSONB, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("order_id", name="uq_order_delivery_order"),
    )
    op.create_index("ix_order_deliveries_order_id", "order_deliveries", ["order_id"])
    op.create_index("ix_order_deliveries_provider", "order_deliveries", ["provider"])
    op.create_index(
        "ix_order_deliveries_courier_status", "order_deliveries", ["courier_status"]
    )
    # A webhook arrives knowing only the courier's own id, so that lookup has to
    # be an index hit rather than a scan of every delivery we have ever made.
    op.create_index(
        "ix_order_deliveries_courier_order_id",
        "order_deliveries",
        ["courier_order_id"],
    )

    for column in (
        sa.Column("delivery_quote_provider", sa.String(length=20), nullable=True),
        sa.Column("delivery_quote_zone", sa.String(length=100), nullable=True),
        sa.Column("delivery_quote_fee", sa.Numeric(10, 2), nullable=True),
        sa.Column("delivery_quote_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("delivery_quote_currency", sa.String(length=3), nullable=True),
        sa.Column("delivery_quote_distance_m", sa.Integer(), nullable=True),
        sa.Column("delivery_quote_reference", sa.String(length=64), nullable=True),
        sa.Column("delivery_quote_latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("delivery_quote_longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("delivery_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_quote_error", sa.Text(), nullable=True),
    ):
        op.add_column("carts", column)


def downgrade() -> None:
    for name in (
        "delivery_quote_error",
        "delivery_quote_at",
        "delivery_quote_longitude",
        "delivery_quote_latitude",
        "delivery_quote_reference",
        "delivery_quote_distance_m",
        "delivery_quote_currency",
        "delivery_quote_cost",
        "delivery_quote_fee",
        "delivery_quote_zone",
        "delivery_quote_provider",
    ):
        op.drop_column("carts", name)

    op.drop_index("ix_order_deliveries_courier_order_id", table_name="order_deliveries")
    op.drop_index("ix_order_deliveries_courier_status", table_name="order_deliveries")
    op.drop_index("ix_order_deliveries_provider", table_name="order_deliveries")
    op.drop_index("ix_order_deliveries_order_id", table_name="order_deliveries")
    op.drop_table("order_deliveries")

    # PostgreSQL cannot drop an enum value without rebuilding the type, and
    # rebuilding it would rewrite every order row. The two extra states are
    # harmless if unused, so they stay — same call migration 023 made.
