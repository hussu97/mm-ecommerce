"""Add a 'voided' purchase-order status and its audit stamps.

Voiding a PO cancels it after the fact: any received stock is reversed (which
restates the weighted-average cost) and the order drops off valuation, spend and
the VAT reclaim, while the row is kept for audit. This widens the
``ck_purchase_orders_status_allowed`` CHECK to admit ``voided`` and adds
``voided_by`` / ``voided_at`` to record who cancelled it and when.

Revision ID: 273_po_status_voided
Revises: 272_customer_directory_cache
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "273_po_status_voided"
down_revision: Union[str, None] = "272_customer_directory_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "'draft', 'pending', 'approved', 'declined', 'partially_received', 'closed'"
_NEW = _OLD + ", 'voided'"


def upgrade() -> None:
    op.add_column(
        "purchase_orders",
        sa.Column("voided_by", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_purchase_orders_voided_by_users",
        "purchase_orders",
        "users",
        ["voided_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "ck_purchase_orders_status_allowed", "purchase_orders", type_="check"
    )
    op.create_check_constraint(
        "ck_purchase_orders_status_allowed",
        "purchase_orders",
        f"status IN ({_NEW})",
    )


def downgrade() -> None:
    # Fold any voided orders back to 'closed' so the narrower CHECK can hold.
    op.execute("UPDATE purchase_orders SET status = 'closed' WHERE status = 'voided'")
    op.drop_constraint(
        "ck_purchase_orders_status_allowed", "purchase_orders", type_="check"
    )
    op.create_check_constraint(
        "ck_purchase_orders_status_allowed",
        "purchase_orders",
        f"status IN ({_OLD})",
    )
    op.drop_constraint(
        "fk_purchase_orders_voided_by_users", "purchase_orders", type_="foreignkey"
    )
    op.drop_column("purchase_orders", "voided_at")
    op.drop_column("purchase_orders", "voided_by")
