"""Freeze the resolved trade-license identity onto each order.

VAT is already frozen on an order (`vat_rate`/`vat_amount` + the `order_taxes`
rows, "stored rather than derived so a VAT return reproduces the invoice"). The
seller's legal identity was not — the receipt read it live off the branch, which
breaks now that a branch trades under different licenses per channel (see
`234_branch_channel_tax_configs`).

These three nullable columns record the identity resolved for the order's
(branch, channel) at creation, so every downstream reader (POS receipt, admin,
storefront, reports) reproduces what the order was actually issued under. Null
means "inherit the branch / business identity", which is every historical order:
forward-only, no backfill.

Revision ID: 235_orders_tax_identity
Revises: 234_branch_channel_tax
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "235_orders_tax_identity"
down_revision: Union[str, None] = "234_branch_channel_tax"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("tax_number", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column("tax_registration_name", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "orders", sa.Column("invoice_title", sa.String(length=120), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "invoice_title")
    op.drop_column("orders", "tax_registration_name")
    op.drop_column("orders", "tax_number")
