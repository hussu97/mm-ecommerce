"""Aggregator order: capture the marketplace's short customer code.

Additive only. Some channels expose two ids: a long machine order id (Noon's
`orderNr`, the scrape's dedup key) AND a short customer-facing code (Noon's
`orderRef`, e.g. "2253") that GrubTech surfaces as its `externalId`. Promotion and
the GrubOps ingest both converge on `orders.external_reference`, but for Noon the
two sides were writing the two different ids, so a Barsha/Sharjah Noon order was
filed twice. `display_ref` stores that short code so convergence can match the two
paths on the shared value. Null for channels whose one id already IS the short code.

Revision ID: 162_agg_display_ref
Revises: 161_agg_clean_slate
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "162_agg_display_ref"
down_revision: Union[str, None] = "161_agg_clean_slate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_order",
        sa.Column("display_ref", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aggregator_order", "display_ref")
