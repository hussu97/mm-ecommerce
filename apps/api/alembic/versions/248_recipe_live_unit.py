"""Stop freezing the ingredient unit onto a recipe line — read it live.

A recipe line consumes an inventory item in that item's *ingredient* unit, and
until now the unit string was snapshotted onto ``recipe_lines.ingredient_unit``
when the draft was authored. The numeric side was already live — cost and
consumption read ``inventory_items.storage_to_ingredient_factor`` at post time —
so the only thing the snapshot bought us was a label that drifted: change an
item's ingredient unit (g → pack) and every existing recipe kept displaying the
old word while the maths moved on.

The unit is now derived live from the item (an association proxy on the model),
so this column is redundant and is dropped. Nothing reads it any more.

Downgrade re-adds it and backfills from the item. Recipe lines on a published
(active/retired) version are immutable via ``recipe_line_immutable``; a straight
UPDATE would trip it, so the backfill runs with that trigger disabled — it is
restoring a derived value, not editing the recipe.

Revision ID: 248_recipe_live_unit
Revises: 247_catalog_sync_outbox
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "248_recipe_live_unit"
down_revision: Union[str, None] = "247_catalog_sync_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("recipe_lines", "ingredient_unit")


def downgrade() -> None:
    op.add_column(
        "recipe_lines",
        sa.Column("ingredient_unit", sa.String(length=30), nullable=True),
    )
    op.execute("ALTER TABLE recipe_lines DISABLE TRIGGER recipe_line_immutable")
    op.execute(
        """
        UPDATE recipe_lines AS rl
        SET ingredient_unit = ii.ingredient_unit
        FROM inventory_items AS ii
        WHERE ii.id = rl.item_id
        """
    )
    op.execute("ALTER TABLE recipe_lines ENABLE TRIGGER recipe_line_immutable")
    op.alter_column("recipe_lines", "ingredient_unit", nullable=False)
