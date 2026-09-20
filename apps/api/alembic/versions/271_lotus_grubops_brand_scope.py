"""Repair and enforce GrubOps brand scope on approved mappings.

Revision ID: 271_lotus_grubops_brand_scope
Revises: 270_recipe_retry_generation
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "271_lotus_grubops_brand_scope"
down_revision: Union[str, None] = "270_recipe_retry_generation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BRAND_ID = "6922ff03323715175fede66b"
_LOTUS_RECIPE_IDS = (
    "6aa931ea0bab35448cae0eff",  # Lotus Cookie Melt (250 grams)
    "6aa931ea0bab35448cae0f00",  # Lotus Cookie Melt (500 grams)
)


def upgrade() -> None:
    # Exact, guarded content repair: only the two known approved recipe mappings
    # and only while their scope is still absent.  A later operator correction is
    # never overwritten by a migration replay.
    op.execute(
        sa.text(
            """
            UPDATE external_item_map
            SET scope = :brand_id,
                updated_at = now()
            WHERE system = 'grubops'
              AND approved IS TRUE
              AND external_ref IN (:recipe_250, :recipe_500)
              AND (scope IS NULL OR btrim(scope) = '')
            """
        ).bindparams(
            brand_id=_BRAND_ID,
            recipe_250=_LOTUS_RECIPE_IDS[0],
            recipe_500=_LOTUS_RECIPE_IDS[1],
        )
    )
    op.create_check_constraint(
        "ck_external_item_map_grubops_scope",
        "external_item_map",
        "system <> 'grubops' OR NOT approved "
        "OR (scope IS NOT NULL AND btrim(scope) <> '')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_external_item_map_grubops_scope",
        "external_item_map",
        type_="check",
    )
    # Deliberately do not erase the repaired brand id: the value is valid domain
    # data, and a downgrade must not resurrect the broken push payload.
