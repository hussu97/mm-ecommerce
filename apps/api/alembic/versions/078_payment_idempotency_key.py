"""Let a retried payment be a no-op instead of a second charge.

`RegisterModel.pay` is three calls — record the payment, close the order, print
— over a 15-second timeout. A payment the server genuinely recorded can time out
on the way back, and the cashier, looking at a check that still reads unpaid,
takes the money again.

The only thing standing in the way was `amount > outstanding`, which catches the
single-tender case and nothing else: on a split payment the second half is a
legitimately smaller amount against a legitimately lower balance, so a replayed
first half passes the guard cleanly.

Nullable and partially indexed, so payments recorded without a key — every row
that exists today, and any client that does not send one — neither collide with
each other nor need backfilling.

Revision ID: 078_payment_idempotency_key
Revises: 077_check_number_unique
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "078_payment_idempotency_key"
down_revision: Union[str, None] = "077_check_number_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_payments",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_order_payments_idempotency_key",
        "order_payments",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_order_payments_idempotency_key", table_name="order_payments")
    op.drop_column("order_payments", "idempotency_key")
