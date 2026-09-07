"""Record when an order's promo redemption was handed back.

`_persist_order` increments `promo_codes.current_uses` when a website order is
written, and nothing ever gave it back: a cancelled or abandoned order went on
holding one of the campaign's redemptions forever, so a coupon capped at
`max_uses` was exhausted by orders that never happened. A code that should have
run all month was "fully claimed" by 200 abandoned checkouts.

`order_lifecycle._consequences` now releases the redemption when an online order
reaches `cancelled` — `current_uses = GREATEST(current_uses - 1, 0)`. This column
is the first-arrival guard: the release stamps it, and a second cancellation of
the same order (cancel → recover → cancel) finds it already set and does not
release twice. Null means the order has not released its use — which is every
order that is not a cancelled website order carrying a code.

Additive and guarded: a nullable timestamp with no default and no backfill. It
describes only what happens from here; the redemptions already stranded by past
cancellations are not chased, because there is no record of which cancelled
orders had incremented a still-live code and which were cancelled before the
increment existed at all.

Revision ID: 206_order_promo_released
Revises: 205_orders_email_lowercase
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "206_order_promo_released"
down_revision: Union[str, None] = "205_orders_email_lowercase"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("promo_released_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "promo_released_at")
