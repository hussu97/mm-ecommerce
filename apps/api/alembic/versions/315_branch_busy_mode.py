"""Busy mode and pay-later checks.

* `branches.busy_since` / `busy_by_user_id` — a branch taking itself offline
  for online orders, thrown from the register. Busy ⇔ `busy_since IS NOT NULL`.
* `branches.pay_later_checks` — the register's "Sent to kitchen" tab: a counter
  check may be fired, set aside and paid after the food is served. Default off;
  switched on for Barsha below.
* `branch_busy_events` — who threw the switch, from which till, when.
* `branch_channel_busy_state` — per (branch, aggregator) what the outlet ought
  to be and how far the reconciler got; also the register banner's chips.
* `aggregator_worker_command` — the durable hand-off to the headed worker for
  Keeta, the one channel the API cannot write itself.
* Permission `pos.branch.busy`, granted to every role that can run the
  register (`pos.register.access`) — busy mode is for whoever is on the counter
  when the rush hits.

Barsha's flag is matched by name, not reference: references differ per
environment (`B001` in prod) and migration 236 set the precedent. Guarded on
`pay_later_checks = false` so it is a no-op once anyone has touched it.

Revision ID: 315_branch_busy_mode
Revises: 314_pnl_net_to_the_fil
Create Date: 2026-10-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "315_branch_busy_mode"
down_revision: Union[str, None] = "314_pnl_net_to_the_fil"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=_NOW,
        ),
    ]


def upgrade() -> None:
    # ─── branches ────────────────────────────────────────────────────────────
    op.add_column(
        "branches", sa.Column("busy_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "branches",
        sa.Column(
            "busy_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "branches",
        sa.Column(
            "pay_later_checks",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ─── branch_busy_events ──────────────────────────────────────────────────
    op.create_table(
        "branch_busy_events",
        _uuid_pk(),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("busy", sa.Boolean(), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW
        ),
        sa.CheckConstraint(
            "source IN ('pos', 'admin')", name="ck_branch_busy_events_source"
        ),
    )
    op.create_index(
        "ix_branch_busy_events_branch_at", "branch_busy_events", ["branch_id", "at"]
    )

    # ─── branch_channel_busy_state ───────────────────────────────────────────
    op.create_table(
        "branch_channel_busy_state",
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("channel", sa.String(20), primary_key=True),
        sa.Column("desired", sa.String(10), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("applied_for_date", sa.Date(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "desired IN ('closed', 'open')",
            name="ck_branch_channel_busy_state_desired",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'failed', 'dry_run', 'unsupported')",
            name="ck_branch_channel_busy_state_status",
        ),
    )
    op.create_index(
        "ix_branch_channel_busy_state_next",
        "branch_channel_busy_state",
        ["status", "next_attempt_at"],
    )

    # ─── aggregator_worker_command ───────────────────────────────────────────
    op.create_table(
        "aggregator_worker_command",
        _uuid_pk(),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_shop_id", sa.String(100), nullable=False),
        sa.Column("desired", sa.String(10), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'failed')",
            name="ck_aggregator_worker_command_status",
        ),
        sa.CheckConstraint(
            "desired IN ('closed', 'open')",
            name="ck_aggregator_worker_command_desired",
        ),
    )
    op.create_index(
        "uq_aggregator_worker_command_pending",
        "aggregator_worker_command",
        ["channel", "kind", "branch_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_aggregator_worker_command_status",
        "aggregator_worker_command",
        ["channel", "status"],
    )

    # ─── permission + Barsha's flag ──────────────────────────────────────────
    op.execute(
        """
        UPDATE roles
        SET permissions = array_append(permissions, 'pos.branch.busy')
        WHERE permissions @> ARRAY['pos.register.access']::varchar[]
          AND NOT (permissions @> ARRAY['pos.branch.busy']::varchar[])
        """
    )
    op.execute(
        """
        UPDATE branches
        SET pay_later_checks = true
        WHERE name ILIKE '%barsha%'
          AND deleted_at IS NULL
          AND pay_later_checks = false
        """
    )


def downgrade() -> None:
    op.execute(
        "UPDATE roles SET permissions = array_remove(permissions, 'pos.branch.busy')"
    )
    op.drop_table("aggregator_worker_command")
    op.drop_table("branch_channel_busy_state")
    op.drop_table("branch_busy_events")
    op.drop_column("branches", "pay_later_checks")
    op.drop_column("branches", "busy_by_user_id")
    op.drop_column("branches", "busy_since")
