"""Aggregator order: a `marketing_fee` for the merchant-funded promotion.

Keeta (and potentially other marketplaces) charge a "Promotion funded by merchant"
back to us — a real cost that reduces our earnings but was captured in no fee
column: it was baked into `net_payable` while `commission_amount` + `payment_fee`
under-reported the true total fees. This adds a distinct column so the promotion is
counted in the fees roll-up without polluting the commission figure (which drives
the effective-rate). Additive, nullable — every other channel simply leaves it NULL,
and a re-scrape/backfill re-parses the stored raw to fill it for existing orders.

Revision ID: 168_agg_marketing_fee
Revises: 167_agg_reauth_backoff
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "168_agg_marketing_fee"
down_revision: Union[str, None] = "167_agg_reauth_backoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column("marketing_fee", sa.Numeric(12, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aggregator_order", "marketing_fee")
