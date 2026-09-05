"""Mirror each branch's weekly schedule out to the integrators.

The weekly schedule (`branch_weekly_hours`) is the single source of truth for
when a branch trades; there is no longer a `Branch.opening_from`/`opening_to`
cache — every "is the branch open / what are today's hours" reader resolves the
window from the schedule on demand via `branch_hours_service`.

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
    """Mirror one branch's weekly schedule out to every integrator it maps to.

    Flushes; the caller commits (a request via `get_db`, or the loop's `_tick`).
    A branch with no weekly schedule has nothing to mirror. The schedule is the
    only place a branch's hours live now — there is no single-window column to
    stamp — so open/closed everywhere is resolved from it on demand.
    """
    day = today or _today()
    sched = await branch_hours_service.schedule(db, branch.id)
    if sched is None:
        return {"branch": branch.name, "status": "no-schedule"}

    open_today = branch_hours_service.window_for(sched, day)
    # Today's window when open, else the next open day's — what Foodics's single
    # daily window is set to (the aggregators get the whole week regardless).
    display = branch_hours_service.effective_window(sched, day)

    await _push_to_channels(db, branch, sched, display=display)
    return {
        "branch": branch.name,
        "status": "closed-today" if open_today is None else "open",
        "window": None if display is None else f"{display[0]}-{display[1]}",
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
        synced = sum(1 for r in results if r.get("status") != "no-schedule")
        if synced:
            logger.info("branch-hours sync: mirrored %s branch(es)", synced)


async def run_forever() -> None:
    """Mirror every branch's weekly schedule to its integrators once an hour."""
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
