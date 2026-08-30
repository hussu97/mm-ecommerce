"""Aggregator session: a `reauth_backoff_until` the worker publishes so the API
knows when a dead channel will next be re-driven.

The worker's heal daemon backs a failing login off exponentially (up to an hour),
persisted to its own volume — invisible to the API. Meanwhile the API's ingest
flags a dead channel and WAITS up to AGGREGATOR_REAUTH_WAIT_SECONDS (360s) for the
worker to bring it back. When the worker is in a long backoff it will not retry
within that window, so the wait was pure dead time ending in RUN_FAILED. This
column lets the worker publish its next-attempt time; the ingest reads it and skips
the wait when the worker demonstrably will not act in time. Additive, nullable;
cleared on every successful session push (a fresh login means healthy).

Revision ID: 167_agg_reauth_backoff
Revises: 166_agg_fulfil_backfill
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "167_agg_reauth_backoff"
down_revision: Union[str, None] = "166_agg_fulfil_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "aggregator_session",
        sa.Column("reauth_backoff_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("aggregator_session", "reauth_backoff_until")
