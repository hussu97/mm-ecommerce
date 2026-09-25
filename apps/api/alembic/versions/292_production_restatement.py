"""Admit the `production_restatement` ledger type.

A production restatement re-costs one batch after the fact — a batch made from
an incomplete recipe carries too little cost, and its stock has usually been
transferred and sold by the time anyone notices. The costing engine prices the
batch at the restated cost from its own posting on (rule 7 in
`costing_engine`), so every transfer and sale drawn from it follows. No stock
moves, so no data changes here: only the type allow-list, spelled out in full
the way migration 225 left it.

Revision ID: 292_production_restatement
Revises: 291_paymob_gateway
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "292_production_restatement"
down_revision: Union[str, None] = "291_paymob_gateway"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WITHOUT = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'waste_from_orders', 'waste_from_production', "
    "'cost_adjustment', 'inventory_count', 'opening_balance', 'internal_use', "
    "'extra_production_use')"
)
_WITH = (
    "type IN ('purchasing', 'transfer_send', 'transfer_receive', "
    "'quantity_adjustment', 'return_to_supplier', 'production', "
    "'consumption_from_production', 'consumption_from_orders', "
    "'return_from_orders', 'waste_from_orders', 'waste_from_production', "
    "'cost_adjustment', 'inventory_count', 'opening_balance', 'internal_use', "
    "'extra_production_use', 'production_restatement')"
)


def _replace(allowed: str) -> None:
    op.drop_constraint(
        "ck_inventory_transactions_type_allowed",
        "inventory_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_transactions_type_allowed", "inventory_transactions", allowed
    )


def upgrade() -> None:
    _replace(_WITH)


def downgrade() -> None:
    _replace(_WITHOUT)
