"""A single dispatch that fails gets tried again, on the ladder batches already use.

`delivery_batches` has carried `attempt_count` / `next_attempt_at` since batching
shipped, and `dispatch_due_batches` sweeps them. `order_deliveries` carried only
`last_error` — so an order that failed on the *un-batched* path was in no batch,
no sweep could ever see it, and the `packed` transition that would have retried
it had already happened. It waited for a human to notice a red box.

MM-20260815-001 waited six hours that way, on an error ("Courier is not
configured") that a deploy had fixed twenty-six minutes after it was written.

Same two column names as the batch table on purpose. A second vocabulary for
"when do we try again" is how the two paths come to disagree about it.

`next_attempt_at` is indexed because it is the sweep's only predicate, and the
sweep runs once a minute forever. Partial, on the rows that have one at all:
the overwhelming majority of deliveries never fail, and an index over all of
them to find the handful that did is most of a table scan wearing a hat.

Revision ID: 094_delivery_dispatch_retry
Revises: 093_auto_accept_external
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "094_delivery_dispatch_retry"
down_revision: Union[str, None] = "093_auto_accept_external"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_deliveries",
        sa.Column(
            "dispatch_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_order_deliveries_next_attempt_at",
        "order_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )

    # Anything already sitting on a failure is handed to the new sweep rather
    # than left where it is. `dispatch_attempts` stays 0, so each of these gets
    # the full ladder — they have never actually been retried, whatever their
    # `last_error` says, because until this migration nothing could.
    #
    # Bounded to orders that are still going somewhere. A cancelled or refunded
    # order with a stale error on its delivery row must not have a driver called
    # for it at the next tick.
    op.execute(
        """
        UPDATE order_deliveries d
           SET next_attempt_at = NOW()
          FROM orders o
         WHERE o.id = d.order_id
           AND d.last_error IS NOT NULL
           AND d.courier_order_id IS NULL
           AND d.batch_id IS NULL
           AND d.provider IN ('lalamove', 'noon_send')
           AND o.status IN ('confirmed', 'packed')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_order_deliveries_next_attempt_at", table_name="order_deliveries")
    op.drop_column("order_deliveries", "next_attempt_at")
    op.drop_column("order_deliveries", "dispatch_attempts")
