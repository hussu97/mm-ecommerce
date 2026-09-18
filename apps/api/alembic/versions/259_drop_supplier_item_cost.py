"""Drop supplier_items.default_unit_cost — cost comes only from purchase orders.

An item's cost is whatever it was actually received at on a purchase order; a
stored "usual" price on the supplier mapping was a second source of truth that
could disagree, so it is removed. The mapping now records only which items a
supplier can supply.

Revision ID: 259_drop_supplier_item_cost
Revises: 258_backfill_cost_layers
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "259_drop_supplier_item_cost"
down_revision: Union[str, None] = "258_backfill_cost_layers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("supplier_items", "default_unit_cost")


def downgrade() -> None:
    op.add_column(
        "supplier_items",
        sa.Column(
            "default_unit_cost",
            sa.Numeric(16, 6),
            nullable=False,
            server_default="0",
        ),
    )
