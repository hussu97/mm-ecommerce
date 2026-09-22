"""Cache geocoded delivery orders for the customer-area map.

The cache is rebuilt atomically with ``customer_cache`` from canonical orders;
it deliberately stores no source address text, only the location and the order
facts used to aggregate customer density, revenue and AOV.

Revision ID: 277_customer_delivery_area_cache
Revises: 279_noon_oms_fee_vat_incl
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "277_customer_delivery_area_cache"
down_revision: Union[str, None] = "279_noon_oms_fee_vat_incl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column("address_geocode_status", sa.String(length=20), nullable=True),
    )
    op.create_check_constraint(
        "ck_aggregator_order_address_geocode_status",
        "aggregator_order",
        "address_geocode_status IS NULL OR address_geocode_status IN "
        "('provided', 'resolved', 'failed', 'outside_uae', 'not_configured')",
    )
    op.create_table(
        "customer_delivery_area_cache",
        sa.Column(
            "order_id",
            sa.UUID(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            sa.UUID(),
            sa.ForeignKey("customer_cache.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("order_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("source_channel", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_customer_delivery_area_cache_customer_id",
        "customer_delivery_area_cache",
        ["customer_id"],
    )
    op.create_index(
        "ix_customer_delivery_area_cache_order_created_at",
        "customer_delivery_area_cache",
        ["order_created_at"],
    )
    # Noon OMS stores some customer pins as E7 integer degrees. Convert only
    # pairs that become valid UAE locations after scaling; source payloads that
    # are missing or genuinely malformed remain untouched. Update both the raw
    # aggregator ledger and its promoted canonical orders, then force the new
    # delivery-area cache's first read to rebuild from the corrected history.
    for table, address, where in (
        ("aggregator_order", "customer_address", "channel = 'noon'"),
        (
            "orders",
            "shipping_address_snapshot",
            "source = 'aggregator' AND aggregator_channel IN ('noon', 'noon_food')",
        ),
    ):
        op.execute(
            f"""
            UPDATE {table}
            SET {address} = jsonb_set(
                jsonb_set({address}, '{{lat}}', to_jsonb(({address}->>'lat')::numeric / 10000000), true),
                '{{lng}}', to_jsonb(({address}->>'lng')::numeric / 10000000), true
            )
            WHERE {where}
              AND COALESCE({address}->>'lat', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
              AND COALESCE({address}->>'lng', '') ~ '^-?[0-9]+(\\.[0-9]+)?$'
              AND abs(({address}->>'lat')::numeric) > 180
              AND abs(({address}->>'lng')::numeric) > 180
              AND ({address}->>'lat')::numeric / 10000000 BETWEEN 22.5 AND 26.5
              AND ({address}->>'lng')::numeric / 10000000 BETWEEN 51.3 AND 56.7
            """
        )
    op.execute("UPDATE customer_cache_state SET dirty = true WHERE id IS TRUE")


def downgrade() -> None:
    op.drop_index(
        "ix_customer_delivery_area_cache_order_created_at",
        table_name="customer_delivery_area_cache",
    )
    op.drop_index(
        "ix_customer_delivery_area_cache_customer_id",
        table_name="customer_delivery_area_cache",
    )
    op.drop_table("customer_delivery_area_cache")
    op.drop_constraint(
        "ck_aggregator_order_address_geocode_status",
        "aggregator_order",
        type_="check",
    )
    op.drop_column("aggregator_order", "address_geocode_status")
