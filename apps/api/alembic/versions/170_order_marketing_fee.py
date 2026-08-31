"""Order.marketing_fee + backfill every promoted order's fees from the scrape.

Two things, both consequences of "aggregator fees come only from scraping":

1. Add `orders.marketing_fee` so the merchant-funded promotion the marketplace
   bills back (scraped onto `aggregator_order.marketing_fee`, migration 168)
   reaches the promoted order, its P&L and the daily report — it had nowhere to
   land before.

2. Backfill history so existing promoted orders match the new rule. Every
   `source='aggregator'` order is re-stamped from its linked `aggregator_order`'s
   scraped figures: commission, payment fee, cancellation fee and the new
   marketing fee. Where the marketplace has not settled the order yet the scraped
   figure is NULL, so the order's fee becomes NULL too — replacing the old static
   configured-rate estimate with the honest "not known yet". Structural backfill,
   carried by the deploy, so history and new promotions read the same.

Revision ID: 170_order_marketing_fee
Revises: 169_static_fee_removal
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "170_order_marketing_fee"
down_revision: Union[str, None] = "169_static_fee_removal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("marketing_fee", sa.Numeric(10, 2), nullable=True),
    )
    # Re-stamp each promoted order from its marketplace ledger. `aggregator_order`
    # links to the MM order via `mm_order_id` (set by promotion). NULL scraped
    # figures become NULL fees — the old modelled estimate is intentionally
    # dropped, not preserved.
    op.execute(
        """
        UPDATE orders o
           SET aggregator_fee  = ao.commission_amount,
               payment_fee     = ao.payment_fee,
               cancellation_fee = ao.cancellation_fee,
               marketing_fee   = ao.marketing_fee
          FROM aggregator_order ao
         WHERE ao.mm_order_id = o.id
           AND o.source = 'aggregator'
        """
    )


def downgrade() -> None:
    op.drop_column("orders", "marketing_fee")
