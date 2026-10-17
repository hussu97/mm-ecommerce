"""Busy mode: a branch taking itself offline for online orders (migration 315).

Three tables, each with one job:

* `branch_busy_events` — the trail. Who threw the switch, from which till, and
  when. Busy mode turns revenue off across five marketplaces and the website,
  so "who did that, and how long was it on" must always have an answer.
* `branch_channel_busy_state` — the work queue *and* the scoreboard. One row
  per (branch, aggregator channel) saying what the outlet ought to be
  (`desired`) and how far we got (`status`). `aggregators/busy_sync` drains it;
  the register's banner reads it to show a chip per channel.
* `aggregator_worker_command` — the hand-off to the headed browser worker for
  the one channel the API cannot write itself (Keeta signs every request
  in-page). Durable rather than in-memory because every deploy stops the
  worker, and a toggle thrown during a cutover still has to land.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, utcnow

__all__ = [
    "AggregatorWorkerCommand",
    "BranchBusyEvent",
    "BranchChannelBusyState",
    "BUSY_SOURCES",
    "BUSY_STATUSES",
    "BUSY_DESIRED",
    "WORKER_COMMAND_STATUSES",
]

BUSY_SOURCES: tuple[str, ...] = ("pos", "admin")
BUSY_DESIRED: tuple[str, ...] = ("closed", "open")
#: `pending` — not yet written (or waiting on the worker); `done` — the portal
#: accepted it; `failed` — the last attempt failed and a retry is scheduled;
#: `dry_run` — the hours write gate is off, so it was planned but not sent;
#: `unsupported` — the outlet cannot be written (unmapped, channel gate off).
BUSY_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "failed",
    "dry_run",
    "unsupported",
)
WORKER_COMMAND_STATUSES: tuple[str, ...] = ("pending", "claimed", "done", "failed")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class BranchBusyEvent(Base, UUIDMixin):
    """One flip of a branch's busy switch."""

    __tablename__ = "branch_busy_events"
    __table_args__ = (
        CheckConstraint(
            f"source IN ({_in(BUSY_SOURCES)})", name="ck_branch_busy_events_source"
        ),
        Index("ix_branch_busy_events_branch_at", "branch_id", "at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    busy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class BranchChannelBusyState(Base, TimestampMixin):
    """What one aggregator outlet ought to be, and how far we got.

    `applied_for_date` is the Dubai day a successful close covers. Every close
    we can make is a "closed today" (today's hours removed from the portal's
    schedule), so a branch still busy after midnight is open again
    on the portal side — the reconciler re-closes any `closed` row whose date is
    behind today.
    """

    __tablename__ = "branch_channel_busy_state"
    __table_args__ = (
        CheckConstraint(
            f"desired IN ({_in(BUSY_DESIRED)})",
            name="ck_branch_channel_busy_state_desired",
        ),
        CheckConstraint(
            f"status IN ({_in(BUSY_STATUSES)})",
            name="ck_branch_channel_busy_state_status",
        ),
        Index("ix_branch_channel_busy_state_next", "status", "next_attempt_at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    channel: Mapped[str] = mapped_column(String(20), primary_key=True)
    desired: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending", default="pending"
    )
    applied_for_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AggregatorWorkerCommand(Base, UUIDMixin, TimestampMixin):
    """A write only the headed worker can make, waiting for it to pick up.

    The partial unique index on pending rows makes the latest toggle win: an
    on → off → on burst before the worker wakes is one command whose `desired`
    was overwritten twice, not three browser runs fighting each other.
    """

    __tablename__ = "aggregator_worker_command"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_in(WORKER_COMMAND_STATUSES)})",
            name="ck_aggregator_worker_command_status",
        ),
        CheckConstraint(
            f"desired IN ({_in(BUSY_DESIRED)})",
            name="ck_aggregator_worker_command_desired",
        ),
        Index(
            "uq_aggregator_worker_command_pending",
            "channel",
            "kind",
            "branch_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_aggregator_worker_command_status", "channel", "status"),
    )

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_shop_id: Mapped[str] = mapped_column(String(100), nullable=False)
    desired: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending", default="pending"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
