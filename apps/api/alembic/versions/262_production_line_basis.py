"""Production lines carry the recipe basis they were raised in.

Admins produce in the recipe's *basis* — units, or batches — not raw owner
units. A batch-basis brookie recipe with ``batch_yield = 22`` means one batch
makes 22 pieces, so the admin enters "1 batch" and the system stores both the
basis count (``planned_basis_quantity``) and the owner-unit truth
(``planned_quantity`` = 22) the ledger and every inventory report keep speaking.

Adds ``basis`` ('unit'/'batch', CHECK), ``batch_yield`` (owner units per batch),
and the basis-unit quantities to ``production_order_items``. All nullable/defaulted
so existing pending lines (raised in owner units) stay valid — they read as unit
basis, and their ``planned_basis_quantity`` falls back to ``planned_quantity``.

Revision ID: 262_production_line_basis
Revises: 261_production_orders
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "262_production_line_basis"
down_revision: Union[str, None] = "261_production_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "production_order_items",
        sa.Column(
            "basis",
            sa.String(length=10),
            nullable=False,
            server_default="unit",
        ),
    )
    op.add_column(
        "production_order_items",
        sa.Column("batch_yield", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "production_order_items",
        sa.Column("planned_basis_quantity", sa.Numeric(16, 4), nullable=True),
    )
    op.add_column(
        "production_order_items",
        sa.Column("produced_basis_quantity", sa.Numeric(16, 4), nullable=True),
    )
    op.create_check_constraint(
        "ck_production_order_items_basis_allowed",
        "production_order_items",
        "basis IN ('unit', 'batch')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_production_order_items_basis_allowed",
        "production_order_items",
        type_="check",
    )
    op.drop_column("production_order_items", "produced_basis_quantity")
    op.drop_column("production_order_items", "planned_basis_quantity")
    op.drop_column("production_order_items", "batch_yield")
    op.drop_column("production_order_items", "basis")
