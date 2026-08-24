"""Every order names the kitchen that made it.

`orders.branch_id` has been nullable since it existed, and for storefront orders
it was always null: nothing set it until `065` mapped polygons to branches, and
even then it was stamped on *after* the insert by a best-effort call that could
quietly decline. So the column meant two different things at once — "the branch
that made this" for a POS order, and "nobody knows" for every website one — and
every reader had to special-case the second.

That is a distinction with no use. An order nobody is making is not a state
worth being able to represent: it prints nowhere, it appears on no register, and
the only way to find it is a query somebody has to think to run.

So the historical rows are filled in and the column is closed. Every existing
null goes to the Sharjah kitchen (`K001`), which is the only place that has ever
made a website order — the second branch does not take them, and the delivery
map has pointed every zone at K001 since `064`.

Order creation now resolves the branch *before* writing the row, through
`order_service.resolve_branch`: the zone's own kitchen, then the configured
pickup branch, then any active branch at all. Only a shop with no active branch
whatsoever fails, and that shop cannot fulfil the order either.

Reversible: the constraint drops cleanly and the backfilled ids stay, because a
branch that made an order is still a true fact about it.

Revision ID: 068_order_branch_required
Revises: 067_order_source_required
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "068_order_branch_required"
down_revision: Union[str, None] = "067_order_source_required"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The only kitchen that has ever made a website order.
FALLBACK_BRANCH_REF = "K001"


def upgrade() -> None:
    conn = op.get_bind()

    branch_id = conn.execute(
        sa.text(
            "SELECT id FROM branches WHERE reference = :ref "
            "AND deleted_at IS NULL ORDER BY is_active DESC LIMIT 1"
        ),
        {"ref": FALLBACK_BRANCH_REF},
    ).scalar()

    if branch_id is None:
        # A database seeded without K001 — a fresh developer machine, or a test
        # fixture. Take the first branch that exists rather than inventing one,
        # and only refuse if there are none at all.
        branch_id = conn.execute(
            sa.text(
                "SELECT id FROM branches WHERE deleted_at IS NULL "
                "ORDER BY is_active DESC, display_order, name LIMIT 1"
            )
        ).scalar()

    orphans = conn.execute(
        sa.text("SELECT count(*) FROM orders WHERE branch_id IS NULL")
    ).scalar()

    if orphans and branch_id is None:
        raise RuntimeError(
            f"{orphans} order(s) have no branch and this database has none to "
            "give them. Seed a branch before migrating."
        )

    if orphans:
        conn.execute(
            sa.text("UPDATE orders SET branch_id = :id WHERE branch_id IS NULL"),
            {"id": str(branch_id)},
        )
        print(f"  filled in the branch for {orphans} order(s)")

    op.alter_column("orders", "branch_id", existing_type=sa.UUID(), nullable=False)


def downgrade() -> None:
    # The ids stay. A branch that made an order is a true fact about it, and
    # putting the nulls back would only recreate the ambiguity this removed.
    op.alter_column("orders", "branch_id", existing_type=sa.UUID(), nullable=True)
