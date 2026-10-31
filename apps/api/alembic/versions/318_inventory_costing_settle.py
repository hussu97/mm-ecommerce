"""Queue for deferred warehouse costing replays.

A till posting the FIFO fast path could not cost on its own (a receipt that
prices waiting stock, a count onto negative stock, …) used to replay its whole
warehouse inside the request — 5-11 s for a produce or a report submit, 28 s
for produce-all, with every other request on the worker waiting behind it. It
now books what it knows and marks its warehouse here; the costing settle loop
replays it within seconds.

A new table rather than a column on ``inventory_costing_dirty``: every posting
that needs a replay upserts that table, so an ``ALTER`` would queue behind
open postings and stall every one after it until it got its lock.

Revision ID: 318_inventory_costing_settle
Revises: 317_customer_cache_change_queue
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "318_inventory_costing_settle"
down_revision: Union[str, None] = "317_customer_cache_change_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_costing_settle",
        sa.Column(
            "warehouse_id",
            sa.UUID(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reason", sa.String(80), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("inventory_costing_settle")
