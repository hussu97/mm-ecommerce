"""What each processor keeps, so the order screen can show what the shop nets.

The order detail page shows what a customer paid and what the courier cost, and
stopped there — so the number nobody could see was the one that matters:
what actually stays. A 45-dirham order with a 24-dirham van and a
2.3-dirham processing fee nets 18.70, and until now working that out meant
somebody opening a Stripe dashboard in another tab.

Both processors charge 2.9% + AED 1 today, which is why those are the defaults.
They are a commercial term and not a constant — rates move with volume — so
they live on the row where a renegotiation is an edit rather than a deploy, and
per gateway because the whole point of having two is that they can differ.

Deliberately an estimate. Stripe's real figure lives on the charge's balance
transaction and does not exist until it settles; this is what the screen can
show the minute an order lands.

Revision ID: 096_gateway_fees
Revises: 095_order_refunds
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "096_gateway_fees"
down_revision: Union[str, None] = "095_order_refunds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_gateways",
        sa.Column(
            "fee_percent",
            sa.Numeric(5, 3),
            nullable=False,
            server_default=sa.text("2.9"),
        ),
    )
    op.add_column(
        "payment_gateways",
        sa.Column(
            "fee_fixed",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column("payment_gateways", "fee_fixed")
    op.drop_column("payment_gateways", "fee_percent")
