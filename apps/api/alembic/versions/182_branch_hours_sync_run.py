"""The run trail for the working-hours fan-out to the integrators.

`branch_hours_sync` mirrors MM's weekly schedule out to each integrator a branch
is mapped to (the five aggregators + Foodics). Until now a per-channel push
failed silently — logged at debug, no history, nothing the admin or Sentry could
read. This is the table that records one outcome per `(branch, channel)` per
tick: the dry-run payload it would send (`planned`), whether it ran live
(`dry_run`), the status, and the error when a portal push fails.

Its own table rather than a `mode` on `aggregator_sync_run`, because that one is
per-channel with no `branch_id` and a channel CHECK that excludes Foodics, and
the hours outcome is per `(branch, channel)`.

Revision ID: 182_branch_hours_sync_run
Revises: 181_seed_sync_to_aggregators
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "182_branch_hours_sync_run"
down_revision: Union[str, None] = "181_seed_sync_to_aggregators"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors app.models.aggregator.HOURS_SYNC_CHANNELS and RUN_STATUSES — spelled
# out here per the string-plus-CHECK convention.
_CHANNELS = "'careem', 'deliveroo', 'talabat', 'noon', 'keeta', 'foodics'"
_STATUSES = "'planned', 'running', 'completed', 'failed', 'partial'"


def upgrade() -> None:
    op.create_table(
        "branch_hours_sync_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column(
            "dry_run", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column("planned", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_branch_hours_sync_run_channel"
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})", name="ck_branch_hours_sync_run_status"
        ),
    )
    op.create_index(
        "ix_branch_hours_sync_run_branch", "branch_hours_sync_run", ["branch_id"]
    )
    op.create_index(
        "ix_branch_hours_sync_run_channel", "branch_hours_sync_run", ["channel"]
    )
    op.create_index(
        "ix_branch_hours_sync_run_created", "branch_hours_sync_run", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_branch_hours_sync_run_created", "branch_hours_sync_run")
    op.drop_index("ix_branch_hours_sync_run_channel", "branch_hours_sync_run")
    op.drop_index("ix_branch_hours_sync_run_branch", "branch_hours_sync_run")
    op.drop_table("branch_hours_sync_run")
