"""Record each ledger movement's storage-unit quantity (the canonical stock unit).

Inventory levels, counts and valuation now read in the item's storage unit
rather than its ingredient unit — grams in the jar, not teaspoons in the recipe
— while recipes stay authored in the ingredient unit and a consumption converts
to storage as it posts. So every ``inventory_transaction_items`` row gains
``quantity_in_storage_unit`` (the canonical figure the level and signed_quantity
are kept in), carried alongside the existing ``quantity_in_ingredient_unit`` so a
movement can show both (e.g. 2 tsp and the 8 g it took off the shelf).

Backfill: at this point every item has a conversion factor of 1 except a few
brand-new dual-unit items with no movements, so the storage figure equals the
ingredient figure for every existing row — a straight copy, changing no value.
The ledger lines are immutable via ``inventory_transaction_line_immutable``,
which yields only to the ``mm.inventory_posting`` flag the poster sets, so the
backfill sets it too.

Revision ID: 246_inventory_storage_canon
Revises: 245_branch_show_recipes
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "246_inventory_storage_canon"
down_revision: Union[str, None] = "245_branch_show_recipes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_transaction_items",
        sa.Column(
            "quantity_in_storage_unit",
            sa.Numeric(16, 4),
            nullable=False,
            server_default="0",
        ),
    )
    # Copy the ingredient figure into the new storage figure. They are equal for
    # every existing row (factor 1), so this rewrites no value; the posting flag
    # lets the UPDATE past the immutable-ledger trigger.
    op.execute("SET LOCAL mm.inventory_posting = 'on'")
    op.execute(
        "UPDATE inventory_transaction_items "
        "SET quantity_in_storage_unit = quantity_in_ingredient_unit"
    )


def downgrade() -> None:
    op.drop_column("inventory_transaction_items", "quantity_in_storage_unit")
