"""Talabat Pro: carry the export's "Is Subscription Order" onto the order.

Adds `aggregator_order.customer_is_member` and backfills it, and the MM order's
existing `orders.aggregator_customer_is_member`, from the Talabat export rows
already stored on `aggregator_order.raw`. Until now that column was null on every
order, because GrubOps sends no Pro signal.

The flag was audited against the fee it drives (2026-09-25). On 597 of 599
billed, delivered "Y" orders Talabat charged its 4 AED "Loyalty Charges - Pro
Delivery Fee"; the 2 others were cash orders it waived. It charged 0 of 462 "N"
orders.

Guarded so it only fills a null. A later value written by the promotion path
wins, and a re-run or a restored database does nothing twice. Raw SQL touches no
ORM `onupdate`, so it doesn't bump `updated_at` and re-trigger promotion.

Revision ID: 289_talabat_pro_flag
Revises: 288_vat_period_charges
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "289_talabat_pro_flag"
down_revision: Union[str, None] = "288_vat_period_charges"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column("customer_is_member", sa.Boolean(), nullable=True),
    )
    op.execute(
        """
        UPDATE aggregator_order
           SET customer_is_member = (upper(btrim(raw->>'Is Subscription Order')) = 'Y')
         WHERE channel = 'talabat'
           AND customer_is_member IS NULL
           AND upper(btrim(raw->>'Is Subscription Order')) IN ('Y', 'N')
        """
    )
    op.execute(
        """
        UPDATE orders o
           SET aggregator_customer_is_member = ao.customer_is_member
          FROM aggregator_order ao
         WHERE ao.mm_order_id = o.id
           AND ao.channel = 'talabat'
           AND ao.customer_is_member IS NOT NULL
           AND o.aggregator_customer_is_member IS NULL
        """
    )


def downgrade() -> None:
    # `orders.aggregator_customer_is_member` predates this migration, so it is
    # left as it is. Only the new aggregator_order column goes.
    op.drop_column("aggregator_order", "customer_is_member")
