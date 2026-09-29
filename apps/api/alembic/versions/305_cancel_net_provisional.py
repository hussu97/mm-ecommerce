"""Orders: whether a marketplace cancellation's settled net is still provisional.

`orders.marketplace_cancellation_provisional` (bool, default false). A Keeta
customer-service cancellation is booked at Keeta's provisional earnings until
its weekly bill lands; this says which ones are still waiting, so the P&L counts
them as "waiting on a fee", and promote overrides the figure with the bill's (or
with 0 when the shop's bill for that week arrives without the order on it).

Revision ID: 305_cancel_net_provisional
Revises: 304_vat_ledger_supplier
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "305_cancel_net_provisional"
down_revision: Union[str, None] = "304_vat_ledger_supplier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "marketplace_cancellation_provisional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "marketplace_cancellation_provisional")
