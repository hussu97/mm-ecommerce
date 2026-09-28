"""What a marketplace settled on a cancelled order.

`orders.marketplace_cancellation_net` (signed): positive when the marketplace
paid the shop on an order it cancelled (Talabat compensation, a Keeta
customer-service cancellation), negative when it charged the shop (a refund after
delivery that kept the commission). Nullable and unset here; `aggregator
promote` fills it on the next pass over each order. See `models.order`.

Revision ID: 300_marketplace_cancellation_net
Revises: 299_category_descriptions
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "300_marketplace_cancellation_net"
down_revision: Union[str, None] = "299_category_descriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("marketplace_cancellation_net", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "marketplace_cancellation_net")
