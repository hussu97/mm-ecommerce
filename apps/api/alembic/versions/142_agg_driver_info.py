"""Carry the aggregator's own rider on the order.

An aggregator order is delivered by the marketplace's rider, not an MM courier,
so it has no `order_drivers` row and the packed screen had no name or number to
show — only the channel logo. GrubOps does report the rider on its `orderDelivery`
block (a name, a mobile, and its own delivery-job status), refreshed as the job
progresses. These three columns hold the little it gives us, so the counter and
the admin can see who is coming and ring them.

Nullable and unconstrained: they are provider words (canon rule 6), null on
every non-aggregator order and until the marketplace assigns a rider. There is
no live driver GPS in the payload, so no distance-from-branch follows — unlike an
MM courier. Filled going forward by the ingest loop; no backfill, because the
raw payloads the loop keeps are re-read each tick anyway.

Revision ID: 142_agg_driver_info
Revises: 141_closed_at_on_closed
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "142_agg_driver_info"
down_revision: Union[str, None] = "141_closed_at_on_closed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("aggregator_driver_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("aggregator_driver_phone", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("aggregator_driver_status", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "aggregator_driver_status")
    op.drop_column("orders", "aggregator_driver_phone")
    op.drop_column("orders", "aggregator_driver_name")
