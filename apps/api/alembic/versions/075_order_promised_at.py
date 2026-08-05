"""Remember what checkout promised, so every email can repeat it.

The checkout tells a customer when their order will arrive. Nothing stored that
answer, so every later screen and email re-derived one — and the two derivations
did not agree.

Checkout asks `delivery_service.estimate_arrival`, which finds the batch window
that is open now and promises `dispatch_at + 1h`. The emails ask
`fulfilment_service._estimate`, which reads the batch off the delivery row — but
a batch is only assigned at PACKED, and the confirmation email is sent at
CONFIRMED. There is no batch yet, so it fell through to a generic
`created_at + 2h prep + 1h drive`.

MM-20260805-008 was placed at 13:45 in a window closing at 18:00. Checkout said
19:00. The confirmation email said 17:25. Both were computed correctly; they were
answering different questions.

Re-deriving the window at send time would not fix it either — by then the window
that was open at checkout may have closed, and the customer would be silently
moved to a later slot nobody told them about. A promise is a fact about what was
said, not a calculation to repeat, so it is stored with the order.

Nullable, with no backfill. An order placed before this column existed has no
recorded promise, and inventing one now would be writing down a sentence nobody
said. `fulfilment_service` keeps its old derivation for exactly those rows.

Revision ID: 075_order_promised_at
Revises: 074_webhook_logs
Create Date: 2026-08-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "075_order_promised_at"
down_revision: Union[str, None] = "074_webhook_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("promised_at", sa.DateTime(timezone=True), nullable=True),
    )
    # `time` or `day`. Carried beside the instant because the two are different
    # promises: "today, 7:00 PM" is ours to make in a zone we dispatch, and
    # "tomorrow" is all that is honest in one a third party collects on a
    # schedule we cannot see. Rendering the second as the first would borrow a
    # precision that belongs to somebody else's van.
    op.add_column(
        "orders",
        sa.Column("promised_precision", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "promised_precision")
    op.drop_column("orders", "promised_at")
