"""Production orders: the production half of a transfer and production order.

Adds `production_orders` and `production_order_items`. An admin raises production
lines at a source branch (alongside a transfer, or standalone); creating one
moves no stock. The source till produces each line later — posting its PRODUCTION
movement then — or cancels it with a note. Movement lives on the line's
``production_transaction_id``; the statuses are String + CHECK.

Also seeds the ``inventory.production.manage`` permission onto every role that
already holds ``inventory.transfers.manage``, so the tills that manage transfers
can manage production without a manual grant.

Revision ID: 261_production_orders
Revises: 260_vat_ledger
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "261_production_orders"
down_revision: Union[str, None] = "260_vat_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "production_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("reference", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "source_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_warehouse_id",
            UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "transfer_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("transfer_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("business_date", sa.String(length=10), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.String(length=64), nullable=True),
        sa.Column("auto_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("reference", name="uq_production_orders_reference"),
        sa.CheckConstraint(
            "status IN ('pending', 'partially_produced', 'produced', 'cancelled')",
            name="ck_production_orders_status_allowed",
        ),
        sa.CheckConstraint(
            r"business_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_production_orders_business_date_format",
        ),
    )
    op.create_index(
        "ix_production_orders_status", "production_orders", ["status"]
    )
    op.create_index(
        "ix_production_orders_source_branch_id",
        "production_orders",
        ["source_branch_id"],
    )
    op.create_index(
        "ix_production_orders_transfer_order_id",
        "production_orders",
        ["transfer_order_id"],
    )
    op.create_index(
        "ix_production_orders_business_date",
        "production_orders",
        ["business_date"],
    )
    op.create_index(
        "uq_production_orders_client_request_id",
        "production_orders",
        ["client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )

    op.create_table(
        "production_order_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("production_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("planned_quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("produced_quantity", sa.Numeric(16, 4), nullable=True),
        sa.Column(
            "unit", sa.String(length=30), nullable=False, server_default="storage"
        ),
        sa.Column(
            "conversion_factor",
            sa.Numeric(16, 6),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("cancel_note", sa.Text(), nullable=True),
        sa.Column(
            "production_transaction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "produced_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'produced', 'cancelled')",
            name="ck_production_order_items_status_allowed",
        ),
    )
    op.create_index(
        "ix_production_order_items_production_order_id",
        "production_order_items",
        ["production_order_id"],
    )
    op.create_index(
        "ix_production_order_items_item_id",
        "production_order_items",
        ["item_id"],
    )
    op.create_index(
        "ix_production_order_items_status",
        "production_order_items",
        ["status"],
    )

    # Grant the new permission to every role that already manages transfers, so
    # the same tills gain "To Produce" without a manual edit. Guarded to roles
    # that hold the transfer permission and do not already have this one, so it is
    # idempotent and never widens a role beyond what it could already do.
    op.execute(
        """
        UPDATE roles
        SET permissions = array_append(permissions, 'inventory.production.manage')
        WHERE permissions @> ARRAY['inventory.transfers.manage']::varchar[]
          AND NOT (permissions @> ARRAY['inventory.production.manage']::varchar[])
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE roles
        SET permissions = array_remove(permissions, 'inventory.production.manage')
        WHERE permissions @> ARRAY['inventory.production.manage']::varchar[]
        """
    )
    op.drop_table("production_order_items")
    op.drop_table("production_orders")
