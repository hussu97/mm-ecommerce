"""Cache the Foodics order id on the GrubOps order map.

MM now drives an aggregator order's lifecycle through Foodics (the POS behind
GrubTech) — accept/dispatch/close/void — instead of GrubOps' force-* overrides.
That needs the order's Foodics id, which GrubOps carries only inside the
`getOrderInfo` history (a code-20000 "…Foodics Order Id: <uuid>" line), never as
a field. The ingest loop parses it once and caches it here so the write-back does
not re-parse the raw payload every push.

Nullable and indexed: null until that history event has been seen for an order
(and on every row that predates this), indexed because the write-back looks a map
row up by `mm_order_id` but the reconcile/debug paths look up by Foodics id. No
backfill — the raw payloads the loop keeps are re-read each tick, so existing
live orders fill in on their next status change.

Revision ID: 146_foodics_order_id
Revises: 145_counter_auto_discount
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "146_foodics_order_id"
down_revision: Union[str, None] = "145_counter_auto_discount"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grubops_order_map",
        sa.Column("foodics_order_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_grubops_order_map_foodics_order_id",
        "grubops_order_map",
        ["foodics_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_grubops_order_map_foodics_order_id",
        table_name="grubops_order_map",
    )
    op.drop_column("grubops_order_map", "foodics_order_id")
