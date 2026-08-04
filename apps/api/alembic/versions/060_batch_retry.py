"""Give a failed run somewhere to record that it should be tried again.

The sweeper only ever selected `PENDING` batches, so a run the courier refused
was the end of the line: it went to `FAILED`, and the packed boxes inside it
waited for somebody to notice and press Dispatch by hand. An empty wallet at
22:30 meant a night's orders sitting in the kitchen.

Two columns are enough to fix it, because the retry queue is just a time:

  `next_attempt_at`   when to try again, or null for a run nothing more will
                      happen to on its own — it went out, or trying again
                      cannot change the answer.
  `attempt_count`     how many times we have tried, which is what picks the
                      delay off the backoff ladder and what stops it.

The sweep reads `next_attempt_at` alone and never looks at status. That is
deliberate: a batch stuck mid-booking because the container restarted, and a
batch whose second courier order failed while the first succeeded, are both runs
with unfinished work — and neither is `FAILED`. One column covers all three
without a status special-case for each.

Existing rows get null and zero: nothing already in the table is owed a retry,
and a `FAILED` batch from before this migration keeps the manual button it has
always had.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "060_batch_retry"
down_revision: Union[str, None] = "059_city_courier_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "delivery_batches",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "delivery_batches",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The sweep runs this lookup every minute for the life of the deployment,
    # and all but a handful of rows are null forever — so the index only carries
    # the runs that actually owe an attempt.
    op.create_index(
        "ix_delivery_batches_next_attempt_at",
        "delivery_batches",
        ["next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_batches_next_attempt_at", table_name="delivery_batches")
    op.drop_column("delivery_batches", "next_attempt_at")
    op.drop_column("delivery_batches", "attempt_count")
