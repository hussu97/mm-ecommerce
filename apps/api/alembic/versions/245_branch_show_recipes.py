"""A branch can show the read-only Recipes tab on its POS terminals.

The Recipes tab is a shop-floor reference for how made items (produced and
semi-finished inventory items) are built from their ingredients. It is off
everywhere by default and turned on per branch from the admin console —
Sharjah first. The flag gates only the tab's visibility on every terminal
paired to the branch; recipes themselves are global, so this changes display,
not data.

Defaults false so no existing branch suddenly grows a tab — a branch is opted
in deliberately from admin, never by this migration guessing.

Revision ID: 245_branch_show_recipes
Revises: 244_recipe_version_batch
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "245_branch_show_recipes"
down_revision: Union[str, None] = "244_recipe_version_batch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "show_recipes",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("branches", "show_recipes")
