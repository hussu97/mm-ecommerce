"""Give every delivery zone the branch that serves it.

A zone already knows what it costs and who carries it. What it did not know is
which kitchen bakes the order — so a website order was priced from a polygon and
then belonged to nobody: `orders.branch_id` stayed null, and no register
anywhere was told an order had arrived.

Exactly one branch per zone. The shapes are disjoint by construction — the
builder punches each smaller circle out of the one around it — so a pin resolves
to one zone and therefore to one kitchen, with no tie-break to invent. A branch
serves many zones: today the Sharjah kitchen serves all eleven.

Every polygon on **every** version is pointed at the Sharjah kitchen, including
the third-party ones. They have no dispatch of their own, but they are still
baked somewhere, and a column that is populated for some rows and null for
others is one that every reader has to special-case. Historical versions are
filled in too, so a rollback of the map does not roll back to zones with no
kitchen.

If `K001` is missing — a database seeded before it existed — the column simply
stays null and everything falls back to the single configured pickup branch,
which is the behaviour before this migration.

Revision ID: 064_polygon_branch
Revises: 063_branch_noon_send_outlet
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "064_polygon_branch"
down_revision: Union[str, None] = "063_branch_noon_send_outlet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The kitchen every zone is served from today.
SHARJAH_BRANCH_REF = "K001"


def upgrade() -> None:
    op.add_column(
        "delivery_polygons",
        sa.Column(
            "branch_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_index(
        "ix_delivery_polygons_branch_id", "delivery_polygons", ["branch_id"]
    )
    op.create_foreign_key(
        "fk_delivery_polygons_branch_id",
        "delivery_polygons",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.get_bind().execute(
        sa.text(
            "UPDATE delivery_polygons SET branch_id = ("
            "  SELECT id FROM branches"
            "  WHERE reference = :reference AND deleted_at IS NULL"
            "  LIMIT 1"
            ")"
        ),
        {"reference": SHARJAH_BRANCH_REF},
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_delivery_polygons_branch_id", "delivery_polygons", type_="foreignkey"
    )
    op.drop_index("ix_delivery_polygons_branch_id", table_name="delivery_polygons")
    op.drop_column("delivery_polygons", "branch_id")
