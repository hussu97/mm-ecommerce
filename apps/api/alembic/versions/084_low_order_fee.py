"""Small-basket surcharge.

AED 15 on any delivery order whose goods come to AED 35 or less, nationwide,
including the zones that deliver free. Free delivery is about what a courier
charges for the run; this is about the fixed cost of baking, boxing and handing
an order over at all, which a 30-dirham basket does not cover however near the
customer lives.

Two columns on `delivery_settings` rather than a constant, because the amount
and the threshold are commercial numbers that will be argued with. A null
threshold disables the fee, which is what it means on every deploy that has not
switched it on.

`orders.low_order_fee` is its own column and not folded into `delivery_fee`:
the two reconcile against different things. The delivery fee is set against
what the courier bills us for that run, and mixing a service charge into it
would make every freight-margin report wrong by fifteen dirhams on exactly the
orders where the margin is tightest.

Revision ID: 084_low_order_fee
Revises: 083_polygon_free_threshold
Create Date: 2026-08-07
"""

from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "084_low_order_fee"
down_revision: Union[str, None] = "083_polygon_free_threshold"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FEE = Decimal("15.00")
THRESHOLD = Decimal("35.00")


def upgrade() -> None:
    op.add_column(
        "delivery_settings",
        sa.Column(
            "low_order_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0.00",
        ),
    )
    op.add_column(
        "delivery_settings",
        sa.Column("low_order_threshold", sa.Numeric(10, 2), nullable=True),
    )
    # Existing orders predate the fee and must keep totalling to what was
    # actually charged, so the backfill is zero and only new orders can carry a
    # non-zero value.
    op.add_column(
        "orders",
        sa.Column(
            "low_order_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
    )

    op.execute(
        sa.text(
            "UPDATE delivery_settings "
            "SET low_order_fee = :fee, low_order_threshold = :threshold"
        ).bindparams(fee=FEE, threshold=THRESHOLD)
    )


def downgrade() -> None:
    op.drop_column("orders", "low_order_fee")
    op.drop_column("delivery_settings", "low_order_threshold")
    op.drop_column("delivery_settings", "low_order_fee")
