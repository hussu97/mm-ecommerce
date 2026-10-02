"""Rename ``purchase_orders.delivery_date`` to ``invoice_date``.

In practice the field holds the date on the supplier's invoice, not a promised
delivery: a till PO is received the moment it is keyed (the date is forced to
the business date), and admin users fill it from the invoice. Its one piece of
logic, the shift report's Received double-entry warning, reads an open PO as
due from this date. A plain rename: every value is kept.

Revision ID: 310_po_invoice_date
Revises: 309_purchase_orders_legal_entity
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "310_po_invoice_date"
down_revision: Union[str, None] = "309_purchase_orders_legal_entity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("purchase_orders", "delivery_date", new_column_name="invoice_date")


def downgrade() -> None:
    op.alter_column("purchase_orders", "invoice_date", new_column_name="delivery_date")
