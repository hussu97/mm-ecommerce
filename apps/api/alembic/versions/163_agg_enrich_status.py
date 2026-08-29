"""Aggregator enrichment: customer address, delivery-agent info, per-order
status history, and a cancellation-fee column on MM orders.

Additive only. Three things the completeness work needs everywhere:

- `aggregator_order` gains a delivery address (JSONB, mirrors
  `orders.shipping_address_snapshot`) and the marketplace's own rider
  (`driver_name`/`driver_phone`/`driver_status`) captured at the provider edge.
- `aggregator_order_status_event` records the marketplace's own status trace
  (Keeta's `merchantOrderTraces`, the Careem/Deliveroo/Talabat timelines) — the
  channel-side twin of `order_status_events`. Keyed `(channel, external_order_id,
  status)` so a re-scrape upserts a step rather than duplicating it.
- `orders.cancellation_fee` — the marketplace's cancellation / customer-
  compensation charge, kept off `aggregator_fee` (commission) and `payment_fee`.

Revision ID: 163_agg_enrich_status
Revises: 162_agg_display_ref
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "163_agg_enrich_status"
down_revision: Union[str, None] = "162_agg_display_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHANNELS_SQL = "'careem', 'deliveroo', 'talabat', 'noon', 'keeta'"


def upgrade() -> None:
    # aggregator_order: customer address + delivery-agent info
    op.add_column(
        "aggregator_order",
        sa.Column("customer_address", JSONB, nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("driver_name", sa.String(120), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("driver_phone", sa.String(30), nullable=True),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("driver_status", sa.String(40), nullable=True),
    )

    # orders: cancellation fee (marketplace cancellation / customer compensation)
    op.add_column(
        "orders",
        sa.Column("cancellation_fee", sa.Numeric(10, 2), nullable=True),
    )

    # aggregator_order_status_event: the marketplace's own status trace
    op.create_table(
        "aggregator_order_status_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_order_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(60), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("raw", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "channel",
            "external_order_id",
            "status",
            name="uq_aggregator_order_status_event",
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS_SQL})",
            name="ck_aggregator_order_status_event_channel",
        ),
    )
    op.create_index(
        "ix_aggregator_order_status_event_order",
        "aggregator_order_status_event",
        ["channel", "external_order_id", "at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aggregator_order_status_event_order",
        table_name="aggregator_order_status_event",
    )
    op.drop_table("aggregator_order_status_event")
    op.drop_column("orders", "cancellation_fee")
    op.drop_column("aggregator_order", "driver_status")
    op.drop_column("aggregator_order", "driver_phone")
    op.drop_column("aggregator_order", "driver_name")
    op.drop_column("aggregator_order", "customer_address")
