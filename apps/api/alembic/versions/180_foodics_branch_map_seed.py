"""Seed foodics_branch_map for the two Foodics-integrated kitchens.

`catalog_sync.integrated_branches()` is "active branches with a Foodics map
row". Live production had zero rows, so even with catalog flags on the Foodics
master-menu path was fiction. This is the initial load — Barsha Heights and
Sharjah Kitchen, the two branches Foodics actually lists — and it is guarded
so it cannot fight the admin: the INSERT runs only while `foodics_branch_map`
is empty, and only for an exact branch name that still exists. Once a human
adds, edits, or deletes a row, the table is no longer empty and a re-run (or a
restored dump that already has maps) matches nothing.

Karama and DSO are aggregator-portal-only; they are not on this Foodics
account and stay unmapped.

Foodics branch ids were read live on 2026-09-04 from the console listing
(`GET /core-api/listing?url=/branches`) against account 862261. They are
account-stable, the same way the Grubtech price-tag id is. MM branch uuids
are resolved by name rather than hardcoded — production happens to be
Barsha `747c717a-a8b6-48d3-ab34-a472f07585ae` and Sharjah
`2ecc7e57-e543-460a-bf2b-5ab5e4642f3b`, but a local DB or a restored dump
with different ids still seeds, and a renamed branch matches nothing.

Both rows are inserted in one statement so the empty-table guard applies to
the pair. Two sequential inserts would seed Barsha and then skip Sharjah
because the table would no longer be empty.

Originally drafted as 179 while local `main` still ended at
`178_per_area_map_v3`. Origin landed `179_per_area_map_v4` first, so this
seed is 180 and revises that head.

Revision ID: 180_foodics_branch_map_seed
Revises: 179_per_area_map_v4
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "180_foodics_branch_map_seed"
down_revision: Union[str, None] = "179_per_area_map_v4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (exact MM branch name, Foodics console branch id)
_SEED: tuple[tuple[str, str], ...] = (
    ("Barsha Heights", "a0371d1d-2971-4ead-9885-e706f01da3d4"),
    ("Sharjah Kitchen", "a0371d1d-1d1f-40f3-834f-5956b94d7b2b"),
)


def upgrade() -> None:
    conn = op.get_bind()
    # One INSERT: empty-table guard is evaluated once against both rows.
    # `:name` is CAST to text so asyncpg sees one type (migration 152's lesson).
    conn.execute(
        text(
            """
            INSERT INTO foodics_branch_map (branch_id, foodics_branch_id, is_active)
            SELECT b.id, v.foodics_id, true
            FROM (VALUES
                    (CAST(:barsha_name AS text), CAST(:barsha_fid AS text)),
                    (CAST(:sharjah_name AS text), CAST(:sharjah_fid AS text))
                 ) AS v(branch_name, foodics_id)
            JOIN branches b
              ON b.name = v.branch_name
             AND b.deleted_at IS NULL
            WHERE NOT EXISTS (SELECT 1 FROM foodics_branch_map)
            ON CONFLICT (branch_id) DO NOTHING
            """
        ),
        {
            "barsha_name": _SEED[0][0],
            "barsha_fid": _SEED[0][1],
            "sharjah_name": _SEED[1][0],
            "sharjah_fid": _SEED[1][1],
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Undo only the exact (name, foodics id) pairs this seed wrote. A human
    # edit of either column no longer matches, so we leave their row alone.
    for name, foodics_id in _SEED:
        conn.execute(
            text(
                """
                DELETE FROM foodics_branch_map AS m
                USING branches AS b
                WHERE m.branch_id = b.id
                  AND b.name = CAST(:name AS text)
                  AND m.foodics_branch_id = CAST(:fid AS text)
                """
            ),
            {"name": name, "fid": foodics_id},
        )
