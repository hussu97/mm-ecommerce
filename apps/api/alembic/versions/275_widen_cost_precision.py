"""Widen per-unit cost columns from Numeric(16,6) to Numeric(20,10).

A per-unit cost is often a batch price divided out over many units (a gram of an
ingredient, a millilitre of syrup). Six decimal places can round such a figure
away, and the error compounds when it is multiplied back over a batch or carried
across a transfer. Per-unit cost is now carried at ten places in the calculation
layer (`app/core/money.py:COST`), so the columns that store it must hold ten
places too — otherwise Postgres silently rounds every write back to six.

This touches only the per-unit **cost** columns. Quantities, conversion factors
and money *totals* (which quantise to the cent or to four places) are unchanged.
Widening is loss-free: every existing 6-place value fits in 20,10.

Revision ID: 275_widen_cost_precision
Revises: 274_drop_product_cost
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "275_widen_cost_precision"
down_revision: Union[str, None] = "274_drop_product_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (table, column) — every per-unit cost store fed by ``money.unit_cost``.
_COST_COLUMNS = [
    ("inventory_levels", "average_cost"),
    ("inventory_transaction_items", "unit_cost"),
    ("inventory_transaction_items", "previous_unit_cost"),
    ("inventory_cost_layers", "unit_cost"),
    ("inventory_cost_layer_consumptions", "unit_cost"),
    ("purchase_order_items", "unit_cost"),
]


def upgrade() -> None:
    for table, column in _COST_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(16, 6),
            type_=sa.Numeric(20, 10),
        )


def downgrade() -> None:
    for table, column in _COST_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(20, 10),
            type_=sa.Numeric(16, 6),
        )
