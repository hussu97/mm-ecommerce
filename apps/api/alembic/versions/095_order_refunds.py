"""Money sent back, recorded where the order can see it.

Until now nothing in this application could issue a refund. The Stripe provider
mapped an inbound `charge.refunded` webhook onto our records, so a refund made
by hand in someone's dashboard was *noticed* — but there was no outbound call
anywhere, and cancelling an order did exactly two things: move the status and
call off the driver. MM-20260815-002 was cancelled with the customer's AED 45
still charged, and nothing about the system said so.

Three columns, and each earns its place:

`orders.refunded_amount` — how much has gone back. Not a boolean, because
partial refunds are the normal case here: the shop refunds the item value and
keeps the delivery fee, since the van was already paid for and often already
drove. Also the guard against refunding twice, which is the failure that costs
real money.

`orders.refunded_at` — when the first refund was issued. Separate from the
status event because a refund can be `pending` at the gateway for days, and
"when did we send it" is a different question from "when did the money land".

`payment_transactions.refund_id` — the gateway's handle for the refund itself,
not the payment. It is what a dashboard conversation quotes, and what makes a
duplicate recognisable at the processor rather than only here.

Revision ID: 095_order_refunds
Revises: 094_delivery_dispatch_retry
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "095_order_refunds"
down_revision: Union[str, None] = "094_delivery_dispatch_retry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "refunded_amount",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "orders",
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("refund_id", sa.String(80), nullable=True),
    )
    # Nothing is backfilled. Refunds made in a gateway dashboard before this
    # existed are real, and this column would be guessing at them — the orders
    # already carry `refunded`/`disputed` statuses from the webhooks that saw
    # them, which is the honest record of what we actually know.


def downgrade() -> None:
    op.drop_column("payment_transactions", "refund_id")
    op.drop_column("orders", "refunded_at")
    op.drop_column("orders", "refunded_amount")
