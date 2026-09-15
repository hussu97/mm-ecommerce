"""Catalog-sync outbox: a durable product-change job the scheduler fans out.

The system half of "menu sync is driven from the admin, not a script". A product
create/update writes one `catalog_sync_outbox` row **in the same transaction**;
the catalog-sync scheduler drains it and runs an idempotent create-or-update of
that product on every integrator it belongs on (name EN/AR, description EN/AR,
price, image). Additive; the drain stays behind `CATALOG_SYNC_ENABLED`.

A partial unique index keeps at most one `pending` row per product, so a burst of
edits coalesces to a single sync (the drain always reads the product's current
state).

Revision ID: 246_catalog_sync_outbox
Revises: 246_inventory_storage_canon
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "247_catalog_sync_outbox"
down_revision: Union[str, None] = "246_inventory_storage_canon"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_sync_outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'error')",
            name="ck_catalog_sync_outbox_status",
        ),
    )
    op.create_index(
        "ix_catalog_sync_outbox_product_id",
        "catalog_sync_outbox",
        ["product_id"],
    )
    # One open job per product — successive edits collapse to a single pending row.
    op.create_index(
        "uq_catalog_sync_outbox_pending",
        "catalog_sync_outbox",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_catalog_sync_outbox_pending", table_name="catalog_sync_outbox")
    op.drop_index("ix_catalog_sync_outbox_product_id", table_name="catalog_sync_outbox")
    op.drop_table("catalog_sync_outbox")
