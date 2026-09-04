"""Keep each branch's derived window — and, later, the integrators — in step
with its weekly schedule.

The weekly schedule (`branch_weekly_hours`) is the source of truth for when a
branch trades; `Branch.opening_from`/`opening_to` is a **cache of today's shift**
that the storefront and the trading-hours engine read. This is what refreshes
that cache: it walks every active branch, resolves today's window from the
schedule — falling through a holiday or a closed weekday to the next open day so
the shown hours are the ones the branch next opens on — and stamps it. Idempotent:
a branch already showing the right window is left untouched, so the loop can run
often and write rarely.

It runs as a background loop (once an hour, like its neighbours in
`app_setup` — no cron in this stack, an advisory lock so a second copy is
harmless, storefront app only) and on demand from the admin "Sync now" button.

Pushing the day's window — and a close on a shut day — to each marketplace is the
next phase: `hours_writers` is the seam, no channel has a writer yet, and it is
gated behind `CATALOG_SYNC_ENABLED`, so today that step logs and skips.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, trading_hours
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.branch import Branch
from app.services import branch_hours_service
from app.services.aggregators import hours_writers

logger = logging.getLogger(__name__)

#: Its own advisory lock, so the register app (or a second storefront slot) does
#: not run a duplicate. A unique key, not shared with the sales report's lock.
_ADVISORY_LOCK_KEY = 0x6D6D_4248_5253_0001

#: Hourly. The work is idempotent and cheap; a shift changes at most at a weekday
#: boundary, and an hour's lag stamping the new day's window is immaterial.
_TICK_SECONDS = 3600


async def _push_to_channels(
    db: AsyncSession, branch: Branch, window: tuple[str, str] | None
) -> None:
    """Best-effort push of the day's state to each channel this branch trades on.

    A no-op until a channel gains a writer — every call raises
    `HoursWriteUnsupported`, which is logged at debug and swallowed. Gated on
    `CATALOG_SYNC_ENABLED` like every other marketplace write.
    """
    if not settings.CATALOG_SYNC_ENABLED:
        return
    for channel in branch.aggregators:
        try:
            if window is None:
                await hours_writers.close_outlet(db, channel=channel, branch=branch)
            else:
                await hours_writers.push_hours(
                    db,
                    channel=channel,
                    branch=branch,
                    opens=window[0],
                    closes=window[1],
                )
        except hours_writers.HoursWriteUnsupported as exc:
            logger.debug("branch-hours push skipped for %s: %s", channel, exc)


async def sync_branch(
    db: AsyncSession, branch: Branch, *, today: date | None = None
) -> dict[str, object]:
    """Refresh one branch's derived window from its schedule and push to channels.

    Flushes; the caller commits (a request via `get_db`, or the loop's `_tick`).
    A branch with no weekly schedule is left on whatever window it has — there is
    nothing to derive, and overwriting it would erase a hand-set fallback.
    """
    day = today or _today()
    sched = await branch_hours_service.schedule(db, branch.id)
    if sched is None:
        return {"branch": branch.name, "status": "no-schedule"}

    open_today = branch_hours_service.window_for(sched, day)
    # What to display: today's window when open, else the next open day's, so a
    # closed day shows the hours it will next open on rather than a stale pair.
    display = open_today or branch_hours_service.next_open_window(sched, day)

    changed = False
    if display is not None:
        opens, closes = display
        if (branch.opening_from, branch.opening_to) != (opens, closes):
            branch.opening_from = opens
            branch.opening_to = closes
            changed = True
    if changed:
        await db.flush()

    await _push_to_channels(db, branch, open_today)
    return {
        "branch": branch.name,
        "status": "closed-today" if open_today is None else "open",
        "window": None if display is None else f"{display[0]}-{display[1]}",
        "updated": changed,
    }


async def sync_all(
    db: AsyncSession, *, today: date | None = None
) -> list[dict[str, object]]:
    """Refresh every active branch. Flushes; the caller commits."""
    branches = (
        (
            await db.execute(
                select(Branch)
                .where(Branch.is_active.is_(True), Branch.deleted_at.is_(None))
                .options(selectinload(Branch.aggregator_maps))
                .order_by(Branch.display_order, Branch.name)
            )
        )
        .scalars()
        .all()
    )
    return [await sync_branch(db, b, today=today) for b in branches]


def _today() -> date:
    """Today on the shop's clock. Containers run on UTC."""
    return datetime.now(trading_hours.TZ).date()


async def _tick(db: AsyncSession) -> None:
    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="branch hours sync") as mine:
        if not mine:
            return
        results = await sync_all(db)
        await db.commit()
        updated = sum(1 for r in results if r.get("updated"))
        if updated:
            logger.info("branch-hours sync: stamped %s branch window(s)", updated)


async def run_forever() -> None:
    """Refresh the derived windows once an hour, forever."""
    logger.info("Branch hours sync loop started")
    while True:
        try:
            async with AsyncSessionFactory() as db:
                await _tick(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Branch hours sync tick failed")
        await asyncio.sleep(_TICK_SECONDS)
