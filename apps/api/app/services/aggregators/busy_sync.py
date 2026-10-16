"""Busy mode, out to the marketplaces: close or reopen each outlet a busy branch
trades on, and keep it that way.

`pos/busy_mode_service.set_busy` writes one `branch_channel_busy_state` row per
(branch, channel) at `pending`; this loop drains them every few seconds. Each
channel closes the way its portal allows:

| channel | close | reopen |
|---|---|---|
| talabat | vendor status `CLOSED_TODAY` | vendor status `OPEN` (unless MM's own schedule has today closed) |
| deliveroo / noon / careem | today's hours removed | the whole week rebuilt from MM (`push_weekly_hours`) |
| keeta | a command for the headed worker (`worker_commands`), which calls the portal's own `set-rest` | the same, `set-open` |

Every close we can make is "closed *today*", so a branch still busy after
midnight is open again on the portal side: a `done` close whose
`applied_for_date` is behind today is re-run. The hourly hours mirror
(`branch_hours_sync`) leaves a channel alone while its row wants it closed —
otherwise it would put today's hours straight back within the hour.

Foodics is deliberately untouched: it is the POS GrubTech injects marketplace
orders into, and closing it could fail orders the marketplace already accepted.

Runs in the scheduler process (the api container), which holds the portal
sessions and the write gates; the register's own process (pos-api) has neither.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, alerting, heartbeat, trading_hours
from app.core.config import settings
from app.core.database import SchedulerSessionFactory
from app.models.aggregator import AggregatorBranchMap, BranchHoursSyncRun
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.busy_mode import BranchChannelBusyState
from app.services import branch_hours_service
from app.services.aggregators import hours_writers, session_store, worker_commands
from app.services.providers.aggregator_base import AggregatorAuthError

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4815
#: Short: a cashier presses the button and watches the chips. A tick with
#: nothing due is one indexed read.
_TICK_SECONDS = 5
#: Retry spacing after a failed write, by attempt; the last value repeats.
_BACKOFF_SECONDS = (5, 15, 60, 300)
#: Alert once a channel has failed this many times in a row — earlier than that
#: is usually a session the worker is already re-warming.
_ALERT_AFTER_ATTEMPTS = 3
#: How long a Keeta chip waits on the worker before the command is re-issued.
_WORKER_WAIT = worker_commands.CLAIM_TIMEOUT
#: Portal writes in flight at once — each holds a scheduler connection.
_PARALLEL = 2
#: Channels closed by rewriting hours — they need MM's schedule to reopen from.
_HOURS_CHANNELS = frozenset({"deliveroo", "noon", "careem"})


def _today() -> date:
    return datetime.now(trading_hours.TZ).date()


def _backoff(attempts: int) -> timedelta:
    index = min(max(attempts, 1), len(_BACKOFF_SECONDS)) - 1
    return timedelta(seconds=_BACKOFF_SECONDS[index])


def _short(error: BaseException | str) -> str:
    text = str(error) or type(error).__name__
    return text[:300]


async def owns_channel(db: AsyncSession, branch_id: uuid.UUID, channel: str) -> bool:
    """Whether busy mode currently owns this outlet's hours (wants it closed).

    `branch_hours_sync` asks before each weekly mirror and skips when true.
    """
    desired = (
        await db.execute(
            select(BranchChannelBusyState.desired).where(
                BranchChannelBusyState.branch_id == branch_id,
                BranchChannelBusyState.channel == channel,
            )
        )
    ).scalar_one_or_none()
    return desired == "closed"


def _due_clause(now: datetime, today: date):
    return or_(
        and_(
            BranchChannelBusyState.status.in_(("pending", "failed")),
            or_(
                BranchChannelBusyState.next_attempt_at.is_(None),
                BranchChannelBusyState.next_attempt_at <= now,
            ),
        ),
        # Midnight: yesterday's "closed today" has lapsed on the portal.
        and_(
            BranchChannelBusyState.desired == "closed",
            BranchChannelBusyState.status == "done",
            BranchChannelBusyState.applied_for_date < today,
        ),
    )


@dataclass(frozen=True)
class _Seen:
    branch_id: uuid.UUID
    channel: str
    desired: str
    updated_at: datetime


async def _due(db: AsyncSession) -> list[_Seen]:
    rows = (
        await db.execute(
            select(
                BranchChannelBusyState.branch_id,
                BranchChannelBusyState.channel,
                BranchChannelBusyState.desired,
                BranchChannelBusyState.updated_at,
            ).where(_due_clause(utcnow(), _today()))
        )
    ).all()
    return [_Seen(*row) for row in rows]


async def _settle(
    db: AsyncSession,
    seen: _Seen,
    *,
    status: str,
    error: str | None = None,
    applied: bool = False,
    next_attempt_at: datetime | None = None,
    attempts: int | None = None,
) -> bool:
    """Write the outcome — only if nobody re-toggled the switch meanwhile.

    The portal call ran without a lock on the row. If staff flipped busy mode
    while it was in flight, `set_busy` rewrote the row (new `desired`, new
    `updated_at`); this compare-and-set then matches nothing, and the next tick
    applies what they want now.
    """
    now = utcnow()
    values: dict[str, object] = {
        "status": status,
        "last_error": error,
        "last_attempt_at": now,
        "next_attempt_at": next_attempt_at,
        "updated_at": now,
    }
    if applied:
        values["applied_for_date"] = _today() if seen.desired == "closed" else None
    if attempts is not None:
        values["attempts"] = attempts
    result = await db.execute(
        update(BranchChannelBusyState)
        .where(
            BranchChannelBusyState.branch_id == seen.branch_id,
            BranchChannelBusyState.channel == seen.channel,
            BranchChannelBusyState.desired == seen.desired,
            BranchChannelBusyState.updated_at == seen.updated_at,
        )
        .values(**values)
    )
    return (result.rowcount or 0) > 0


async def _record_run(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID,
    channel: str,
    op: str,
    status: str,
    dry_run: bool,
    endpoint: str | None = None,
    error: str | None = None,
    started: datetime,
) -> None:
    db.add(
        BranchHoursSyncRun(
            branch_id=branch_id,
            channel=channel,
            status=status,
            dry_run=dry_run,
            planned={"op": op, "channel": channel, "endpoint": endpoint},
            error=error,
            started_at=started,
            finished_at=utcnow(),
        )
    )


async def _queue_keeta(db: AsyncSession, branch: Branch, seen: _Seen) -> None:
    if not settings.CATALOG_SYNC_ENABLED:
        await _settle(
            db,
            seen,
            status="unsupported",
            error="hours writes are disabled (CATALOG_SYNC_ENABLED)",
        )
        return
    shop = (
        await db.execute(
            select(AggregatorBranchMap.external_outlet_id).where(
                AggregatorBranchMap.channel == "keeta",
                AggregatorBranchMap.branch_id == branch.id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not shop:
        await _settle(db, seen, status="unsupported", error="no Keeta shop mapped")
        return
    await worker_commands.supersede_open(
        db,
        channel="keeta",
        kind=worker_commands.KEETA_SHOP_STATUS,
        branch_id=branch.id,
    )
    await worker_commands.queue(
        db,
        channel="keeta",
        kind=worker_commands.KEETA_SHOP_STATUS,
        branch_id=branch.id,
        external_shop_id=shop,
        desired=seen.desired,
    )
    # Still pending: the chip moves when the worker reports back
    # (`record_worker_outcome`). If it never does, re-issue after the wait.
    await _settle(
        db,
        seen,
        status="pending",
        next_attempt_at=utcnow() + _WORKER_WAIT,
    )


async def record_worker_outcome(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID,
    channel: str,
    desired: str,
    ok: bool,
    error: str | None,
    dry_run: bool = False,
) -> None:
    """The worker finished a busy-mode command; move the chip if still wanted."""
    row = await db.get(BranchChannelBusyState, (branch_id, channel))
    if row is None or row.desired != desired:
        return
    now = utcnow()
    if ok:
        row.status = "dry_run" if dry_run else "done"
        row.last_error = None
        row.attempts = 0
        row.next_attempt_at = None
        row.applied_for_date = _today() if desired == "closed" else None
    else:
        row.attempts = (row.attempts or 0) + 1
        row.status = "failed"
        row.last_error = (error or "the Keeta worker could not write")[:300]
        row.next_attempt_at = now + _backoff(row.attempts)
    row.last_attempt_at = now
    row.updated_at = now
    db.add(
        BranchHoursSyncRun(
            branch_id=branch_id,
            channel=channel,
            status="completed" if ok else "failed",
            dry_run=dry_run,
            planned={
                "op": "busy_close" if desired == "closed" else "busy_open",
                "channel": channel,
                "endpoint": "headed worker",
            },
            error=None if ok else row.last_error,
            started_at=now,
            finished_at=now,
        )
    )


async def _write(db: AsyncSession, branch: Branch, seen: _Seen) -> str | None:
    """Make one outlet match `seen.desired`. Returns the endpoint written.

    Raises `HoursWriteUnsupported` for an outlet we cannot write at all, and
    whatever the portal raised otherwise.
    """
    dry = not settings.BRANCH_HOURS_SYNC_LIVE
    channel = seen.channel
    sched = await branch_hours_service.schedule(db, branch.id)
    if channel in _HOURS_CHANNELS and sched is None:
        # Closing removes today's hours and reopening rebuilds the week from
        # MM's schedule. With no schedule to rebuild from, a close could not be
        # undone — so it is not made.
        raise hours_writers.HoursWriteUnsupported(
            "no MM weekly schedule to restore the hours from"
        )
    if seen.desired == "closed":
        plan = await hours_writers.close_outlet(
            db, channel=channel, branch=branch, dry_run=dry
        )
        return str(plan.get("endpoint"))
    if channel == "talabat":
        if branch_hours_service.window_for(sched, _today()) is None and sched:
            # A day the branch is closed anyway: `OPEN` would contradict it.
            return None
        plan = await hours_writers.reopen_outlet(
            db, channel=channel, branch=branch, dry_run=dry
        )
        return str(plan.get("endpoint"))
    assert sched is not None
    plan = await hours_writers.push_weekly_hours(
        db, channel=channel, branch=branch, weekly=sched, dry_run=dry
    )
    return str(plan.get("endpoint"))


async def apply_one(seen: _Seen) -> str:
    """Bring one (branch, channel) to what busy mode wants. Own session, commits."""
    async with SchedulerSessionFactory() as db:
        branch = await db.get(
            Branch, seen.branch_id, options=[selectinload(Branch.aggregator_maps)]
        )
        if branch is None:
            return "missing"
        # Read before any rollback below: an expired attribute on an async
        # session is a lazy load, which raises outside the greenlet.
        branch_id, branch_name = branch.id, branch.name
        op = "busy_close" if seen.desired == "closed" else "busy_open"
        if seen.channel == "keeta":
            await _queue_keeta(db, branch, seen)
            await db.commit()
            return "queued"

        started = utcnow()
        dry = not settings.BRANCH_HOURS_SYNC_LIVE
        attempts = await _attempts(db, seen)
        try:
            endpoint = await _write(db, branch, seen)
        except hours_writers.HoursWriteUnsupported as exc:
            await db.rollback()
            await _settle(db, seen, status="unsupported", error=_short(exc))
            await db.commit()
            logger.info("busy-mode %s %s unsupported: %s", op, seen.channel, exc)
            return "unsupported"
        except Exception as exc:  # noqa: BLE001 — a portal/session failure
            await db.rollback()
            attempts += 1
            if isinstance(exc, AggregatorAuthError):
                await session_store.mark_needs_bootstrap(
                    db, seen.channel, error=str(exc)
                )
            error = _short(exc)
            await _settle(
                db,
                seen,
                status="failed",
                error=error,
                attempts=attempts,
                next_attempt_at=utcnow() + _backoff(attempts),
            )
            await _record_run(
                db,
                branch_id=branch_id,
                channel=seen.channel,
                op=op,
                status="failed",
                dry_run=dry,
                error=error,
                started=started,
            )
            await db.commit()
            logger.warning(
                "busy-mode %s failed for %s at %s (attempt %s): %s",
                op,
                seen.channel,
                branch_name,
                attempts,
                exc,
            )
            if attempts == _ALERT_AFTER_ATTEMPTS:
                alerting.capture_issue(
                    f"busy mode could not {'close' if op == 'busy_close' else 'reopen'} "
                    f"{seen.channel} at {branch_name}",
                    level="error",
                    fingerprint=["busy-mode", seen.channel, op],
                    tags={"channel": seen.channel, "branch": branch_name, "op": op},
                )
            return "failed"

        status = "dry_run" if dry else "done"
        await _settle(
            db,
            seen,
            status=status,
            error=None,
            applied=True,
            attempts=0,
            next_attempt_at=None,
        )
        await _record_run(
            db,
            branch_id=branch_id,
            channel=seen.channel,
            op=op,
            status="completed",
            dry_run=dry,
            endpoint=endpoint,
            started=started,
        )
        await db.commit()
        logger.info("busy-mode %s %s at %s: %s", op, seen.channel, branch_name, status)
        return status


async def _attempts(db: AsyncSession, seen: _Seen) -> int:
    return (
        await db.execute(
            select(BranchChannelBusyState.attempts).where(
                BranchChannelBusyState.branch_id == seen.branch_id,
                BranchChannelBusyState.channel == seen.channel,
            )
        )
    ).scalar_one_or_none() or 0


async def _tick() -> int:
    """Apply every due row. Returns how many were attempted."""
    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="busy sync") as mine:
        if not mine:
            return 0
        async with SchedulerSessionFactory() as db:
            due = await _due(db)
        # Channels in parallel, a few at a time: a Noon session timing out must
        # not hold Talabat's close behind it, and each runs on its own session
        # from the scheduler's six-connection pool — so bounded, not unbounded.
        gate = asyncio.Semaphore(_PARALLEL)

        async def _bounded(seen: _Seen) -> str:
            async with gate:
                return await apply_one(seen)

        results = await asyncio.gather(
            *(_bounded(seen) for seen in due), return_exceptions=True
        )
        for seen, result in zip(due, results):
            if isinstance(result, BaseException):
                logger.error(
                    "busy-mode apply crashed for %s/%s",
                    seen.branch_id,
                    seen.channel,
                    exc_info=result,
                )
        return len(due)


async def run_forever() -> None:
    """Drain busy-mode work every few seconds, forever."""
    logger.info("Busy-mode sync loop started")
    while True:
        try:
            await heartbeat.beat("busy_sync")
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Busy-mode sync tick failed")
        await asyncio.sleep(_TICK_SECONDS)
