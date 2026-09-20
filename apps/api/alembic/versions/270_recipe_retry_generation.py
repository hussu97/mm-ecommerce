"""Gate pending recipe retries on active-catalog changes.

Revision ID: 270_recipe_retry_generation
Revises: 269_report_movement_window
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "270_recipe_retry_generation"
down_revision: Union[str, None] = "269_report_movement_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recipe_catalog_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.CheckConstraint("id = 1", name="ck_recipe_catalog_state_singleton"),
        sa.CheckConstraint(
            "generation > 0", name="ck_recipe_catalog_state_positive_generation"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Generation one represents the catalog already present at deployment.  Old
    # pending rows start at zero and receive one final attempt; if still missing,
    # they sleep until a later activation advances the clock.
    op.execute("INSERT INTO recipe_catalog_state (id, generation) VALUES (1, 1)")
    op.add_column(
        "inventory_source_events",
        sa.Column(
            "recipe_catalog_generation",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("inventory_source_events", "recipe_catalog_generation")
    op.drop_table("recipe_catalog_state")
