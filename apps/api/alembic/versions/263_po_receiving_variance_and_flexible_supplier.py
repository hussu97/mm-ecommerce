"""PO receiving variance + supplier flexible item mapping.

Receiving a purchase order now captures a short/excess variance the way a
transfer receipt does: ``purchase_order_items.variance_reason`` holds the
receiver's note when what arrived differs from what was ordered.

``suppliers.allow_any_item`` turns on "flexible item mapping" — a PO for that
supplier may then add any active purchasable inventory item, not only the mapped
ones (default false: strict, mapped-only).

Revision ID: 263_po_receiving_variance
Revises: 262_production_line_basis
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "263_po_receiving_variance"
down_revision: Union[str, None] = "262_production_line_basis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_order_items",
        sa.Column("variance_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "suppliers",
        sa.Column(
            "allow_any_item",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("suppliers", "allow_any_item")
    op.drop_column("purchase_order_items", "variance_reason")
