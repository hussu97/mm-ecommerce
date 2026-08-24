"""A closed check must carry the moment it closed.

The hour/terminal/cashier reports read the time an order closed, and an
aggregator order used to reach pos_status='closed' with a null closed_at because
nobody rings it up (the counter stamps it at payment; migration 139 backfilled
the history; the GrubOps ingest now stamps it going forward). This adds the
guard so it cannot silently regress: any order marked closed has a closed_at.

Nullable-by-necessity is preserved — the check is conditional on pos_status, so
an active check (no close time yet) and a storefront order (pos_status null) are
both untouched. Only 'closed' rows are held to it.

Belt first: a tiny backfill runs immediately before the constraint, catching any
order that closed in the window between 139 and this deploy, so ADD CONSTRAINT
cannot fail on a straggler.

Revision ID: 141_closed_at_on_closed
Revises: 140_courier_branch_rates
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "141_closed_at_on_closed"
down_revision: Union[str, None] = "140_courier_branch_rates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT = "ck_orders_closed_has_closed_at"


def upgrade() -> None:
    op.execute(
        """
        UPDATE orders
        SET closed_at = COALESCE(updated_at, created_at)
        WHERE pos_status = 'closed'
          AND closed_at IS NULL
        """
    )
    # NULL pos_status (storefront orders) passes: `NULL <> 'closed'` is NULL,
    # which a CHECK treats as satisfied. Only a genuinely 'closed' row is held to
    # having a closed_at.
    op.create_check_constraint(
        _CONSTRAINT,
        "orders",
        "pos_status <> 'closed' OR closed_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "orders", type_="check")
