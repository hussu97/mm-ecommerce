"""Promotion link on aggregator_order.

Order promotion turns a scraped `aggregator_order` into a real MM `orders` row
(for all four branches; a records mirror, never touching the POS). It needs a
link back — `aggregator_order.mm_order_id` / `promoted_at`: the MM order this
became (or the GrubOps order it converged onto) and when promotion last synced
it, so `promote_channel` is incremental (only new-or-changed orders) the way
`reconcile_channel` is.

The convergence key it relies on already exists: `uq_orders_source_external_reference`,
a partial unique index on `orders (source, external_reference)` where
`source='aggregator'`. GrubOps ingest and promotion both set `external_reference`
to the marketplace's own order id, so that constraint already forbids two MM
orders for the same aggregator order — a Barsha/Sharjah order GrubOps later
delivers adopts the promotion gap-fill rather than duplicating it, and a re-run
never inserts twice. No new index is added here.

Revision ID: 156_agg_order_promotion
Revises: 155_noon_account_config
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "156_agg_order_promotion"
down_revision: Union[str, None] = "155_noon_account_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column(
            "mm_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "aggregator_order",
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_aggregator_order_promoted",
        "aggregator_order",
        ["channel", "promoted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_aggregator_order_promoted", table_name="aggregator_order")
    op.drop_column("aggregator_order", "promoted_at")
    op.drop_column("aggregator_order", "mm_order_id")
