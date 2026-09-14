"""Let a recipe version be authored per-batch instead of per-unit.

``recipe_versions.basis`` is 'unit' (the default, and every pre-existing row) or
'batch'. When 'batch', ``batch_yield`` records how many owner units one batch
makes, so consuming N units draws ``N / batch_yield`` of the version's lines.
Consumption stays at the ingredient-unit level — the divisor is applied during
recipe expansion, so the ledger and shift reports are unchanged. Basis is per
version, so a recipe can move between unit and batch across versions.

Revision ID: 244_recipe_version_batch
Revises: 243_menu_group_pdf_exclude
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "244_recipe_version_batch"
down_revision: Union[str, None] = "243_menu_group_pdf_exclude"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "recipe_versions",
        sa.Column(
            "basis",
            sa.String(length=10),
            nullable=False,
            server_default="unit",
        ),
    )
    op.add_column(
        "recipe_versions",
        sa.Column("batch_yield", sa.Numeric(20, 8), nullable=True),
    )
    op.create_check_constraint(
        "ck_recipe_version_basis",
        "recipe_versions",
        "basis IN ('unit', 'batch')",
    )
    op.create_check_constraint(
        "ck_recipe_version_batch_yield",
        "recipe_versions",
        # NULL > 0 is NULL and a CHECK passes on NULL, so the batch branch must
        # assert IS NOT NULL explicitly or a batch row with no yield slips through.
        "(basis = 'unit' AND batch_yield IS NULL) "
        "OR (basis = 'batch' AND batch_yield IS NOT NULL AND batch_yield > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_recipe_version_batch_yield", "recipe_versions", type_="check"
    )
    op.drop_constraint("ck_recipe_version_basis", "recipe_versions", type_="check")
    op.drop_column("recipe_versions", "batch_yield")
    op.drop_column("recipe_versions", "basis")
