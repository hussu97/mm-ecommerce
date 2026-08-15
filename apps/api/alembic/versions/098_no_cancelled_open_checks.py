"""A cancelled order may not still be a live check.

The two columns tell one story from two rooms: `status` is what the website and
the console believe, `pos_status` is what the till believes. They were kept in
step by convention alone, and convention lost — production had cashier orders
sitting `cancelled` with `pos_status = 'active'`, so the iPad showed a live
check for a sale that no longer existed, took new items onto it, and counted it
towards the day.

`order_lifecycle.transition` now owns the pairing in code: cancelling voids any
check still open. This puts the same fact into the database, where a writer
that bypasses the lifecycle — a manual UPDATE, a script, next year's bug —
cannot re-create the contradiction. The first statement repairs the rows that
already carry it, with the same answer the lifecycle gives: void, not closed,
because closed means paid and finished and this was neither.

Only the one known-impossible pairing is constrained. The other combinations —
an undelivered order on a still-open check, say — are questions the shop has
not answered, and a CHECK constraint is a place to record answers, not to
invent them.

Revision ID: 098_no_cancelled_open_checks
Revises: 097_drop_device_receives_online
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "098_no_cancelled_open_checks"
down_revision: Union[str, None] = "097_drop_device_receives_online"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: What "still open on the register" means, mirrored from
#: `order_lifecycle._OPEN_ON_THE_REGISTER` — spelled out here because a
#: migration must say what it did even after the code moves on.
OPEN = "('draft', 'pending', 'active')"


def upgrade() -> None:
    op.execute(
        "UPDATE orders SET pos_status = 'void' "
        f"WHERE status = 'cancelled' AND pos_status IN {OPEN}"
    )
    op.create_check_constraint(
        "ck_orders_cancelled_check_not_open",
        "orders",
        f"NOT (status = 'cancelled' AND pos_status IN {OPEN})",
    )


def downgrade() -> None:
    # The voided rows stay voided: they were wrong before this migration and
    # the repair is not something a rollback should undo.
    op.drop_constraint("ck_orders_cancelled_check_not_open", "orders", type_="check")
