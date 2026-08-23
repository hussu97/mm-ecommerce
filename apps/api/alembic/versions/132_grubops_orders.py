"""Ingest aggregator orders from GrubOps into the shared `orders` table.

The OOS sync (migration 131) pushes availability out to GrubOps. This is the
return path: a background loop pulls Talabat/Noon/Careem/Deliveroo/Keeta orders
out of GrubOps and records each as an ordinary `orders` row with
`source='aggregator'`, so MM becomes the single book of record across counter,
website and aggregator sales.

Two structural pieces:

* `grubops_order_map` — one row per GrubOps order. Dedupe (unique on GrubOps's
  own order id), the last status seen (so a quiet poll costs nothing), and the
  write-back state. Reasoning is on `app/models/grubops_order.py`.
* `orders.aggregator_channel` — the customer-facing channel name for an
  aggregator order, plus a partial-unique index on `(source, external_reference)`
  so a double-ingest can never create two orders for the same aggregator sale
  even if the map row is bypassed.

Schema only. `source='aggregator'` is a new value of the existing unconstrained
`orders.source` string, so there is no CHECK to widen and no native enum to
`ALTER TYPE`. The loop fills everything; the migration seeds nothing.

Revision ID: 132_grubops_orders
Revises: 131_grubops_mapping
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "132_grubops_orders"
down_revision: Union[str, None] = "131_grubops_mapping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("aggregator_channel", sa.String(length=40), nullable=True),
    )
    # Idempotency backstop independent of grubops_order_map: one aggregator
    # order (one external reference) can exist once. Partial so it constrains
    # only aggregator rows and leaves the null external_reference on every
    # website/counter order alone.
    op.create_index(
        "uq_orders_source_external_reference",
        "orders",
        ["source", "external_reference"],
        unique=True,
        postgresql_where=sa.text("source = 'aggregator'"),
    )

    op.create_table(
        "grubops_order_map",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("grubops_order_id", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("source_channel", sa.String(length=40), nullable=True),
        sa.Column("location_id", sa.String(length=64), nullable=True),
        sa.Column(
            "mm_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_grubops_status", sa.String(length=40), nullable=True),
        sa.Column("last_pushed_status", sa.String(length=40), nullable=True),
        sa.Column("last_push_error", sa.String(length=500), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
    )
    op.create_index(
        "ix_grubops_order_map_grubops_order_id",
        "grubops_order_map",
        ["grubops_order_id"],
        unique=True,
    )
    op.create_index(
        "ix_grubops_order_map_mm_order_id",
        "grubops_order_map",
        ["mm_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_grubops_order_map_mm_order_id", table_name="grubops_order_map")
    op.drop_index(
        "ix_grubops_order_map_grubops_order_id", table_name="grubops_order_map"
    )
    op.drop_table("grubops_order_map")
    op.drop_index("uq_orders_source_external_reference", table_name="orders")
    op.drop_column("orders", "aggregator_channel")
