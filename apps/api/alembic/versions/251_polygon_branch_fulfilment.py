"""Give every delivery zone an ordered list of branches that can serve it.

A zone named exactly one kitchen (`delivery_polygons.branch_id`) and one courier
(`fulfilment_provider`). That is becoming the *preferred* branch of an ordered
list: a zone can now be served by several kitchens, and the checkout walks the
list in `rank` order and gives the order to the first branch that can make the
whole basket.

This migration only builds the table and backfills it so that **nothing
changes**: every polygon on every version gets exactly one assignment, rank 1,
copied byte-for-byte from its existing `branch_id` / `fulfilment_provider` /
`alternate_providers`. So the redefined "rank-1 mirror" reads back identical to
today's single-branch behaviour. Adding a second branch to any zone (Barsha) is a
separate, later seed migration that carries the simulation's frozen output — kept
apart precisely because it is the part that changes what customers see.

A polygon whose `branch_id` is null (a database seeded before `064`, or a
hand-built zone) is backfilled to Sharjah/`K001` when that branch exists, and
skipped entirely when it does not — the same "if K001 is missing, do nothing and
fall back to the configured pickup branch" guard `064` uses.

Revision ID: 251_polygon_branch_fulfilment
Revises: 250_product_consumes_stock
Create Date: 2026-09-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "251_polygon_branch_fulfilment"
down_revision: Union[str, None] = "250_product_consumes_stock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The kitchen a polygon with no explicit branch falls back to — the one every
#: zone was pointed at by `064`.
SHARJAH_BRANCH_REF = "K001"

#: Mirror of `FulfilmentProviderEnum`. Spelled out here rather than imported: a
#: migration has to keep describing the schema as it was on the day it ran, so it
#: cannot depend on a model that will keep changing.
_PROVIDERS = ("lalamove", "noon_send", "slider_bike", "slider_car", "third_party")


def upgrade() -> None:
    op.create_table(
        "polygon_branch_fulfilment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("polygon_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "fulfilment_provider",
            sa.String(length=20),
            server_default="third_party",
            nullable=False,
        ),
        sa.Column(
            "alternate_providers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["polygon_id"], ["delivery_polygons.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "polygon_id", "branch_id", name="uq_polygon_branch_fulfilment_branch"
        ),
        sa.UniqueConstraint(
            "polygon_id", "rank", name="uq_polygon_branch_fulfilment_rank"
        ),
        sa.CheckConstraint(
            "fulfilment_provider IN ({})".format(
                ", ".join(f"'{p}'" for p in _PROVIDERS)
            ),
            name="ck_polygon_branch_fulfilment_provider",
        ),
        sa.CheckConstraint(
            "rank >= 1", name="ck_polygon_branch_fulfilment_rank_positive"
        ),
    )
    op.create_index(
        "ix_polygon_branch_fulfilment_polygon_id",
        "polygon_branch_fulfilment",
        ["polygon_id"],
    )
    op.create_index(
        "ix_polygon_branch_fulfilment_branch_id",
        "polygon_branch_fulfilment",
        ["branch_id"],
    )

    # Rank-1 backfill over every polygon on every version. `branch_id` is copied
    # verbatim where set; a null one resolves to Sharjah, and if Sharjah is
    # absent the COALESCE yields null, the WHERE drops the row, and that polygon
    # is simply left with no assignment (runtime falls back exactly as before).
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO polygon_branch_fulfilment
                (id, polygon_id, branch_id, rank, fulfilment_provider, alternate_providers)
            SELECT
                gen_random_uuid(),
                p.id,
                COALESCE(
                    p.branch_id,
                    (SELECT id FROM branches
                     WHERE reference = :reference AND deleted_at IS NULL
                     LIMIT 1)
                ),
                1,
                p.fulfilment_provider,
                p.alternate_providers
            FROM delivery_polygons p
            WHERE COALESCE(
                p.branch_id,
                (SELECT id FROM branches
                 WHERE reference = :reference AND deleted_at IS NULL
                 LIMIT 1)
            ) IS NOT NULL
            """
        ),
        {"reference": SHARJAH_BRANCH_REF},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_polygon_branch_fulfilment_branch_id",
        table_name="polygon_branch_fulfilment",
    )
    op.drop_index(
        "ix_polygon_branch_fulfilment_polygon_id",
        table_name="polygon_branch_fulfilment",
    )
    op.drop_table("polygon_branch_fulfilment")
