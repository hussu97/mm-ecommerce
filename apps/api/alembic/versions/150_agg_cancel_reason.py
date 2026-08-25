"""GrubOps's own reason for cancelling an aggregator order.

When the marketplace cancels an order its reason (`TOO_BUSY`, `ITEM_OUT_OF_STOCK`,
…) rides on the GrubOps payload as `orderHeader.cancelReason`, and we were
dropping it: the order flipped to `cancelled` and admin showed nothing about why.
This adds the column the ingest fills from that field, mirroring
`aggregator_driver_status` next door — a provider-verbatim word, so **no CHECK**
(canon rule 6: provider columns stay unconstrained; a new GrubOps reason code
should never need a migration).

Nullable, and no backfill: a website/counter cancellation has no aggregator
reason, and past aggregator cancellations kept the reason only on a
`grubops_order_map.raw` snapshot that may since have been overwritten. New
cancellations fill it going forward.

Revision ID: 150_agg_cancel_reason
Revises: 149_single_counter_order_type
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "150_agg_cancel_reason"
down_revision: Union[str, None] = "149_single_counter_order_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("aggregator_cancel_reason", sa.String(length=60), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "aggregator_cancel_reason")
