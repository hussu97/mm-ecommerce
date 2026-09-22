"""Supplier misc-item tag and purchase_order_misc_items.

Some suppliers (e.g. Amazon AE) are used to buy one-off items for specific
orders that are **not** part of tracked inventory — they arrive on the same
invoice and we still want them for expense history and VAT recovery, but they
must never become inventory items or FIFO stock. ``suppliers.allows_misc_items``
tags such a supplier (it then appears in the PO supplier picker even with no
mapped items); ``purchase_order_misc_items`` holds the free-text lines, mirroring
the money fields of ``purchase_order_items`` so PO totals and the VAT reclaim
report include them, without any link to ``inventory_items``.

Revision ID: 276_po_misc_items
Revises: 275_widen_cost_precision
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "276_po_misc_items"
down_revision: Union[str, None] = "275_widen_cost_precision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "suppliers",
        sa.Column(
            "allows_misc_items",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "purchase_order_misc_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "purchase_order_id",
            sa.UUID(),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Free text: what was bought. Deliberately NO item_id — a misc line is
        # never an inventory item and never posts to stock.
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("storage_unit", sa.String(length=30), nullable=False),
        # Money mirrors purchase_order_items: entered_total is gross (VAT-
        # inclusive), vat_amount is the recoverable slice, net_total = gross-vat,
        # unit_cost is gross per unit for report parity.
        sa.Column(
            "entered_total", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("vat_amount", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("net_total", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(20, 10), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_purchase_order_misc_items_quantity"
        ),
    )


def downgrade() -> None:
    op.drop_table("purchase_order_misc_items")
    op.drop_column("suppliers", "allows_misc_items")
