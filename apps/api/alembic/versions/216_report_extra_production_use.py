"""Raw-material report: extra_production_consumption_quantity column.

Two changes for the new "Extra production use" sheet column — a shop-entered
additional raw-material drawdown for consumption the recipe does not capture
(off-recipe use, or producing a good with no item/recipe in the system):

1. One additive, NOT NULL column (server default 0) on
   ``shift_inventory_report_lines``. Every existing line predates the feature and
   gets 0, which is exactly right: no report carried an extra drawdown before it
   existed.
2. Widen the ``ck_inventory_transactions_type_allowed`` CHECK (first spelled out in
   migration 186) to admit the new ``extra_production_use`` ledger type the column
   posts on approval.

Revision ID: 216_report_extra_production_use
Revises: 215_delivery_version_revision
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "216_report_extra_production_use"
down_revision: Union[str, None] = "215_delivery_version_revision"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The full allow-list, mirroring InventoryTransactionTypeEnum. Spelled out in one
# string so the widened constraint reads the same as migration 186's original.
_TYPES_WITH_EXTRA = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'return_from_transfers', 'waste_from_orders', "
    "'waste_from_production', 'cost_adjustment', 'inventory_count', "
    "'opening_balance', 'internal_use', 'extra_production_use')"
)
_TYPES_WITHOUT_EXTRA = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'return_from_transfers', 'waste_from_orders', "
    "'waste_from_production', 'cost_adjustment', 'inventory_count', "
    "'opening_balance', 'internal_use')"
)


def upgrade() -> None:
    op.add_column(
        "shift_inventory_report_lines",
        sa.Column(
            "extra_production_consumption_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
    )
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        _TYPES_WITH_EXTRA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        _TYPES_WITHOUT_EXTRA,
    )
    op.drop_column(
        "shift_inventory_report_lines", "extra_production_consumption_quantity"
    )
