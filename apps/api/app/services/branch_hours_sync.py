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

Mirroring the schedule out to the integrators is gated behind
`CATALOG_SYNC_ENABLED` (master write gate) and stays dry-run until
`BRANCH_HOURS_SYNC_LIVE`. The five aggregators get the WHOLE weekly schedule via
`hours_writers.push_weekly_hours` (talabat/deliveroo/noon/careem over httpx);
Foodics gets today's single branch window; Keeta's in-page write is the headed
worker's job and is skipped here. Every (branch, channel) outcome — dry-run plan
or live result — is recorded to `branch_hours_sync_run`, and a portal failure is
raised to Sentry with a stable per-channel fingerprint.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, alerting, trading_hours
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.aggregator import BranchHoursSyncRun, FoodicsBranchMap
from app.models.base import utcnow
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


def _dry_run() -> bool:
    """Hours writes stay dry-run (log + record, no POST) until the live flag.

    `CATALOG_SYNC_ENABLED` is the master gate; `BRANCH_HOURS_SYNC_LIVE` takes
    only the hours fan-out live, so we can dry-run-enumerate on the VM, audit,
    then flip — without also enabling menu/price writes.
    """
    return not settings.BRANCH_HOURS_SYNC_LIVE


def _plan_summary(plan: dict[str, object]) -> dict[str, object]:
    """A JSON-safe digest of a writer plan for the run log (drops the session)."""
    return {
        k: plan.get(k)
        for k in ("channel", "op", "endpoint", "weekly", "window", "dry_run")
        if k in plan
    }


async def _record_run(
    db: AsyncSession,
    *,
    branch: Branch,
    channel: str,
    status: str,
    dry_run: bool,
    planned: dict[str, object] | None = None,
    error: str | None = None,
    started: datetime | None = None,
) -> None:
    """Persist one (branch, channel) outcome. Flushes; the caller commits."""
    db.add(
        BranchHoursSyncRun(
            branch_id=branch.id,
            channel=channel,
            status=status,
            dry_run=dry_run,
            planned=planned,
            error=error,
            started_at=started,
            finished_at=utcnow(),
        )
    )


async def _push_weekly_to_channel(
    db: AsyncSession, branch: Branch, channel: str, sched: dict[int, tuple[str, str]]
) -> None:
    """Mirror the whole weekly schedule to one aggregator; record the outcome.

    `HoursWriteUnsupported` (gate off, unmapped outlet) is a debug skip with no
    row. Any other error is recorded `failed`, alerted to Sentry with a stable
    per-channel fingerprint, and swallowed so the next channel still runs.
    """
    dry = _dry_run()
    started = utcnow()
    try:
        plan = await hours_writers.push_weekly_hours(
            db, channel=channel, branch=branch, weekly=sched, dry_run=dry
        )
    except hours_writers.HoursWriteUnsupported as exc:
        logger.debug("branch-hours weekly push skipped for %s: %s", channel, exc)
        return
    except Exception as exc:  # noqa: BLE001 — a dead session/portal, not a bug
        logger.warning("branch-hours weekly push failed for %s: %s", channel, exc)
        tags = {
            "channel": channel,
            "branch_id": str(branch.id),
            "op": "push_weekly_hours",
        }
        alerting.capture_issue(
            f"branch-hours weekly push failed: {channel}",
            level="warning",
            fingerprint=["branch-hours-sync", channel, "weekly-push"],
            tags={**tags, "dry_run": str(dry)},
        )
        alerting.capture_exc(exc, tags=tags)
        await _record_run(
            db,
            branch=branch,
            channel=channel,
            status="failed",
            dry_run=dry,
            error=str(exc),
            started=started,
        )
        return
    await _record_run(
        db,
        branch=branch,
        channel=channel,
        status="completed",
        dry_run=dry,
        planned=_plan_summary(plan),
        started=started,
    )


async def _push_foodics(
    db: AsyncSession, branch: Branch, display: tuple[str, str] | None
) -> None:
    """Set the Foodics branch's single daily window to today's MM window.

    Foodics carries one opening window per branch (not a weekly schedule), so it
    is resynced daily to whatever window MM shows for today — the same `display`
    (today's, or the next open day's) stamped on the storefront cache. Skipped
    when the branch has no active Foodics map or MM has no schedule.
    """
    if display is None:
        return
    row = (
        await db.execute(
            select(FoodicsBranchMap).where(
                FoodicsBranchMap.branch_id == branch.id,
                FoodicsBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    dry = _dry_run()
    started = utcnow()
    opens, closes = display
    plan = {
        "op": "push_foodics_hours",
        "channel": "foodics",
        "endpoint": f"PUT /core-api/updating /branches/{row.foodics_branch_id}",
        "window": f"{opens}-{closes}",
        "dry_run": dry,
    }
    try:
        if not dry:
            from app.services.providers import foodics_provider as fp

            await fp.provider.set_branch_hours(
                row.foodics_branch_id, opening_from=opens, opening_to=closes
            )
    except Exception as exc:  # noqa: BLE001 — Foodics session/console failure
        logger.warning("branch-hours foodics push failed: %s", exc)
        tags = {
            "channel": "foodics",
            "branch_id": str(branch.id),
            "op": "push_foodics_hours",
        }
        alerting.capture_issue(
            "branch-hours foodics push failed",
            level="warning",
            fingerprint=["branch-hours-sync", "foodics", "daily-push"],
            tags={**tags, "dry_run": str(dry)},
        )
        alerting.capture_exc(exc, tags=tags)
        await _record_run(
            db,
            branch=branch,
            channel="foodics",
            status="failed",
            dry_run=dry,
            error=str(exc),
            started=started,
        )
        return
    await _record_run(
        db,
        branch=branch,
        channel="foodics",
        status="completed",
        dry_run=dry,
        planned=plan,
        started=started,
    )


async def _push_to_channels(
    db: AsyncSession,
    branch: Branch,
    sched: dict[int, tuple[str, str]],
    *,
    display: tuple[str, str] | None,
) -> None:
    """Mirror MM's hours to every integrator this branch is mapped to.

    A no-op until `CATALOG_SYNC_ENABLED`. The five aggregators get the whole
    weekly schedule (`push_weekly_hours`); Foodics gets today's single window.
    Keeta is skipped here — its in-page write is the headed worker's job and is
    recorded from the worker's result POST.
    """
    if not settings.CATALOG_SYNC_ENABLED:
        return
    for channel in branch.aggregators:
        if channel == "keeta":
            continue
        await _push_weekly_to_channel(db, branch, channel, sched)
    await _push_foodics(db, branch, display)


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

    await _push_to_channels(db, branch, sched, display=display)
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
