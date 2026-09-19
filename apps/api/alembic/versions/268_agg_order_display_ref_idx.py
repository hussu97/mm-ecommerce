"""Index aggregator_order(channel, display_ref) so correlation stops scanning.

The order-correlation lookup — "which MM order is this marketplace id?" — was the
single most expensive query in the database (28.9% of all exec time; ~75ms mean
over ~38k calls, growing with the table). It matches one ref against
`external_order_id` OR `display_ref` OR the Deliveroo-only JSON path
`raw->detail->drn_id`. The JSON branch is unindexable, and its presence in the OR
forced a full per-channel scan with a JSONB parse on every row.

The service now adds the JSON branch only for Deliveroo (whose ids genuinely live
in three places); every other channel is `external_order_id IN (…) OR display_ref
IN (…)`. `external_order_id` already has the unique index
`uq_aggregator_order(channel, external_order_id)`; this adds the matching partial
index for `display_ref`, so Postgres can BitmapOr the two instead of scanning.

Partial (`WHERE display_ref IS NOT NULL`) because only noon and deliveroo populate
it — a few hundred rows — so the index stays tiny and only covers the rows the
OR branch can match. Not unique: a channel's `display_ref` (the short daily
ticket) repeats across days.

Revision ID: 268_agg_order_display_ref_idx
Revises: 267_drop_inventory_item_cost
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "268_agg_order_display_ref_idx"
down_revision: Union[str, None] = "267_drop_inventory_item_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_aggregator_order_channel_display_ref",
        "aggregator_order",
        ["channel", "display_ref"],
        postgresql_where=sa.text("display_ref IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_aggregator_order_channel_display_ref",
        table_name="aggregator_order",
    )
