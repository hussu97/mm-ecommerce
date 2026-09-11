"""Inventory accounting clarity: drop the unused transfer-return type, add an
Adjustments reconciliation column.

1. Remove ``return_from_transfers`` from the allowed transaction types. It was
   defined, signed and reference-prefixed but no service ever posted one, so it
   only advertised a movement the ledger never made. No rows use it (nothing
   creates it), so narrowing the CHECK cannot fail.
2. Add ``shift_inventory_report_lines.adjustment_quantity`` — a ledger-filled,
   read-only column carrying the net of every movement in the window that has no
   column of its own (a customer restock, a manual adjustment, a return to
   supplier). Without it those movements folded silently into Opening and the
   reconciliation did not tie; the count's variance was measured against an
   incomplete closing.

Revision ID: 225_inventory_accounting_fixes
Revises: 224_transfer_client_request_id
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "225_inventory_accounting_fixes"
down_revision: Union[str, None] = "224_transfer_client_request_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The allow-list without / with return_from_transfers. Spelled out in full so the
# constraint reads the same way migration 216 left it.
_WITHOUT_RFT = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'waste_from_orders', 'waste_from_production', "
    "'cost_adjustment', 'inventory_count', 'opening_balance', 'internal_use', "
    "'extra_production_use')"
)
_WITH_RFT = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'return_from_transfers', 'waste_from_orders', "
    "'waste_from_production', 'cost_adjustment', 'inventory_count', "
    "'opening_balance', 'internal_use', 'extra_production_use')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        _WITHOUT_RFT,
    )
    op.add_column(
        "shift_inventory_report_lines",
        sa.Column(
            "adjustment_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("shift_inventory_report_lines", "adjustment_quantity")
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        _WITH_RFT,
    )
