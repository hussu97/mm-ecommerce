"""Busy mode — a branch taking itself offline for online orders.

Thrown from the register (or the console) as an on/off switch, not a window:
it stays on until somebody turns it off. Turning it on does three things, all
from this one function so no caller can do two of them:

* **The website**, immediately. `Branch.is_busy` is read by every query that
  routes a website order to a branch (`order_service.select_fulfilment` and its
  fallbacks, the pickup points, the catalogue union), so the next request after
  the commit already routes around the branch. The catalogue cache is retired
  so a stale page does not list a product only this branch could make.
* **Every aggregator outlet**, within seconds. One `branch_channel_busy_state`
  row per channel the branch trades on, `pending`; the scheduler's
  `aggregators.busy_sync` loop drains them. The work is not done here because a
  portal write is somebody else's API and can take seconds or fail, and the
  cashier who pressed the button is owed an answer about their own shop.
* **The other registers at the branch**, via a quiet push, so every till shows
  the banner without waiting for its two-minute heartbeat.

Flushes; the request's `get_db` commits.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.busy_mode import BranchBusyEvent, BranchChannelBusyState
from app.models.user import User
from app.schemas.pos.busy_mode import BusyChannelResponse, BusyStatusResponse
from app.services import push_service
from app.services.catalog import catalogue_cache

logger = logging.getLogger(__name__)

#: How each channel is named on the banner. The keys are `AggregatorBranchMap`
#: channels; anything unlisted is title-cased.
CHANNEL_LABELS: dict[str, str] = {
    "talabat": "Talabat",
    "deliveroo": "Deliveroo",
    "noon": "Noon",
    "careem": "Careem",
    "keeta": "Keeta",
}


@dataclass(frozen=True)
class ChannelChip:
    channel: str
    label: str
    #: `closing` / `closed` / `opening` / `open` / `failed` / `manual` / `not_live`
    state: str
    error: str | None


@dataclass(frozen=True)
class BusyState:
    busy: bool
    since: datetime | None
    by_name: str | None
    channels: list[ChannelChip]


def chip_state(row: BranchChannelBusyState) -> str:
    """One channel's state as the register shows it.

    `manual` is a channel we cannot write at all (no session, unmapped outlet):
    staff have to close it in the partner app themselves, and saying so is the
    point of the chip. `not_live` is the hours write gate being off — planned,
    deliberately not sent.
    """
    if row.status == "unsupported":
        return "manual"
    if row.status == "dry_run":
        return "not_live"
    if row.status == "failed":
        return "failed"
    closing = row.desired == "closed"
    if row.status == "done":
        return "closed" if closing else "open"
    return "closing" if closing else "opening"


async def _actor_name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    user = await db.get(User, user_id)
    if user is None:
        return None
    return user.display_name or user.email


async def status(db: AsyncSession, branch: Branch) -> BusyState:
    rows = (
        (
            await db.execute(
                select(BranchChannelBusyState)
                .where(BranchChannelBusyState.branch_id == branch.id)
                .order_by(BranchChannelBusyState.channel)
                # `set_busy` upserts these in SQL, not through the session, so a
                # copy already in the identity map would read back stale.
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return BusyState(
        busy=branch.is_busy,
        since=branch.busy_since,
        by_name=await _actor_name(db, branch.busy_by_user_id),
        channels=[
            ChannelChip(
                channel=row.channel,
                label=CHANNEL_LABELS.get(row.channel, row.channel.title()),
                state=chip_state(row),
                error=row.last_error
                if row.status in ("failed", "unsupported")
                else None,
            )
            for row in rows
        ],
    )


async def set_busy(
    db: AsyncSession,
    branch: Branch,
    busy: bool,
    *,
    actor_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    source: str,
) -> BusyState:
    """Turn busy mode on or off. Idempotent: the same state twice is a no-op.

    A no-op deliberately does not re-queue the channels. A second "on" from
    another till must not restart a close that is already half done, and a
    channel stuck at `failed` is retried by the reconciler's backoff, not by
    staff pressing the button again.
    """
    if busy == branch.is_busy:
        return await status(db, branch)

    now = utcnow()
    branch.busy_since = now if busy else None
    branch.busy_by_user_id = actor_id if busy else None
    db.add(
        BranchBusyEvent(
            branch_id=branch.id,
            busy=busy,
            actor_user_id=actor_id,
            device_id=device_id,
            source=source,
            at=now,
        )
    )

    desired = "closed" if busy else "open"
    for channel in branch.aggregators:
        stmt = insert(BranchChannelBusyState).values(
            branch_id=branch.id,
            channel=channel,
            desired=desired,
            status="pending",
            attempts=0,
            last_error=None,
            applied_for_date=None,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    BranchChannelBusyState.branch_id,
                    BranchChannelBusyState.channel,
                ],
                set_={
                    "desired": desired,
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                    "applied_for_date": None,
                    "next_attempt_at": now,
                    "updated_at": now,
                },
            )
        )
    await db.flush()

    # A busy branch must stop gating (and stop offering) the storefront at once.
    await catalogue_cache.retire()
    logger.info(
        "Busy mode %s at %s (source=%s, actor=%s)",
        "ON" if busy else "OFF",
        branch.name,
        source,
        actor_id,
    )
    try:
        await push_service.notify_branch_busy(
            db, branch_id=branch.id, branch_name=branch.name, busy=busy
        )
    except Exception:  # noqa: BLE001 — a push is a courtesy; the heartbeat catches up
        logger.warning("busy-mode push failed for %s", branch.name, exc_info=True)
    return await status(db, branch)


def to_response(state: BusyState) -> BusyStatusResponse:
    return BusyStatusResponse(
        busy=state.busy,
        since=state.since,
        by_name=state.by_name,
        channels=[
            BusyChannelResponse(
                channel=chip.channel,
                label=chip.label,
                state=chip.state,  # type: ignore[arg-type]
                error=chip.error,
            )
            for chip in state.channels
        ],
    )
