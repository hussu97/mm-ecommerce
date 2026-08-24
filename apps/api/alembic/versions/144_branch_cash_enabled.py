"""A branch can be cashless — no till drawer.

A production kitchen that only fulfils aggregator and website orders never opens
a cash drawer. Until now every branch was made to count a float at each shift
open and close, which reconciles nothing there and puts a phantom float on the
books. `cash_enabled` lets such a branch skip the opening-float entry and the
close-time cash count; the till-close report then drops its cash-reconciliation
lines and prints only the channel revenue summary.

Defaults true so every existing branch keeps counting cash exactly as before —
a cashless branch is switched off deliberately from the admin console, never by
this migration guessing which one.

Revision ID: 144_branch_cash_enabled
Revises: 143_agg_customer_backfill
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "144_branch_cash_enabled"
down_revision: Union[str, None] = "143_agg_customer_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "cash_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )


def downgrade() -> None:
    op.drop_column("branches", "cash_enabled")
