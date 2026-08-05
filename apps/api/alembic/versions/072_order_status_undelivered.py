"""Give a failed handover a status of its own.

A rider who reaches the door and cannot hand the parcel over is not the same
thing as a rider who is still on the way, and until now the orders table could
not tell them apart. `undelivered` lived only on `order_deliveries.courier_status`
with an error string beside it, while the order itself stayed `out_for_delivery`.

MM-20260805-006 is what that looks like in practice: the admin screen and the
customer's track page both said the cake was coming, for hours after noon Send
had reported that it was not. The status was wrong, not merely incomplete.

It is deliberately **not** terminal. The order is paid for, the box exists, and
the ordinary answer is a second attempt — so `order_service.VALID_TRANSITIONS`
leads back out of it to `packed`, `out_for_delivery` and `delivered`, as well as
to `cancelled` and `refunded` for the times it does not.

`ALTER TYPE … ADD VALUE` is one-way. Postgres has no `DROP VALUE`, and rebuilding
the enum would mean rewriting the `orders.status` column with a lock over the
whole table — an unreasonable price for undoing a value that no row can hold
unless something deliberately set it. `downgrade` therefore rewrites any such
rows back to `out_for_delivery`, which is the state they would have been in
before this existed, and leaves the label in place.

`COMMIT` before the `ALTER TYPE`: until Postgres 12 a new enum label could not be
used in the same transaction that added it, and alembic runs migrations inside
one. The commit is harmless on every version and removes the version-dependence.

Revision ID: 072_order_status_undelivered
Revises: 071_order_locale
Create Date: 2026-08-05
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "072_order_status_undelivered"
down_revision: Union[str, None] = "071_order_locale"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'undelivered' "
        "AFTER 'delivered'"
    )


def downgrade() -> None:
    # The label stays; only the rows holding it are put back. See the docstring.
    op.execute(
        "UPDATE orders SET status = 'out_for_delivery' WHERE status = 'undelivered'"
    )
