"""Drop the single `branches.opening_from` / `opening_to` window.

`branch_weekly_hours` (migration 173) is the single source of truth for when a
branch trades, and `branch_hours_service` resolves the window for any day. The
two `opening_from`/`opening_to` columns were a derived cache of today's shift,
stamped daily and read by the storefront, POS and delivery-promise engine.
Every one of those readers now resolves its window from the weekly schedule on
demand, so the cache is redundant — and a second, separately-stampable answer to
"when is the branch open" is exactly the kind of drift we removed batching to
avoid. This drops it.

`business_day_start` and `inventory_end_of_day_time` are NOT touched — they are
the accounting-day rollover, a different concept from the opening window.

Revision ID: 184_drop_branch_opening_window
Revises: 183_agg_order_channel_uq
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "184_drop_branch_opening_window"
down_revision: Union[str, None] = "183_agg_order_channel_uq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("branches", "opening_from")
    op.drop_column("branches", "opening_to")


def downgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "opening_from",
            sa.String(length=5),
            nullable=False,
            server_default="00:00",
        ),
    )
    op.add_column(
        "branches",
        sa.Column(
            "opening_to",
            sa.String(length=5),
            nullable=False,
            server_default="23:59",
        ),
    )
