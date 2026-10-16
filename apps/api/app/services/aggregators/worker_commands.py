"""Writes only the headed `aggregator-worker` can make, queued for it to pick up.

Keeta signs every request in-page (`mtgsig`), so nothing in the API can close a
Keeta outlet: the worker's persistent Chrome profile has to. The worker exposes
no port, so the hand-off is a table it polls through the push-token API —
`queue` here, `claim` and `complete` behind `/aggregators/worker/commands/*`.

Durable rather than in-memory because every deploy stops the worker. The
partial unique index on pending rows makes the latest request win: on → off →
on before the worker wakes is one command, not three browser runs.

Flushes; the caller commits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.busy_mode import AggregatorWorkerCommand

logger = logging.getLogger(__name__)

#: The one command kind today: open or close a Keeta shop for busy mode.
KEETA_SHOP_STATUS = "keeta_shop_status"

#: A claim older than this is presumed lost (the worker died or a deploy stopped
#: it mid-run) and the command is handed out again. Longer than the worker's own
#: per-job budget, so a slow run is not double-issued.
CLAIM_TIMEOUT = timedelta(minutes=12)


async def queue(
    db: AsyncSession,
    *,
    channel: str,
    kind: str,
    branch_id: uuid.UUID,
    external_shop_id: str,
    desired: str,
) -> None:
    """Ask the worker to make `desired` true. Replaces any not-yet-claimed ask."""
    now = utcnow()
    stmt = insert(AggregatorWorkerCommand).values(
        id=uuid.uuid4(),
        channel=channel,
        kind=kind,
        branch_id=branch_id,
        external_shop_id=external_shop_id,
        desired=desired,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["channel", "kind", "branch_id"],
            # Literal, not a bound parameter: Postgres infers the partial index
            # from this predicate, and cannot from `status = $1`.
            index_where=text("status = 'pending'"),
            set_={
                "desired": desired,
                "external_shop_id": external_shop_id,
                "updated_at": now,
            },
        )
    )
    await db.flush()


async def waiting(db: AsyncSession, *, channel: str) -> int:
    """How many commands the worker should run — pending, or a lost claim.

    The worker's cheap poll: it only enqueues a browser job when this is
    non-zero, and the job claims at run time, so a command never sits
    `claimed` behind a long job and times out before it is even started.
    """
    now = utcnow()
    return int(
        (
            await db.execute(
                select(func.count()).where(
                    AggregatorWorkerCommand.channel == channel,
                    or_(
                        AggregatorWorkerCommand.status == "pending",
                        (AggregatorWorkerCommand.status == "claimed")
                        & (AggregatorWorkerCommand.claimed_at < now - CLAIM_TIMEOUT),
                    ),
                )
            )
        ).scalar_one()
    )


async def claim(db: AsyncSession, *, channel: str) -> list[AggregatorWorkerCommand]:
    """Hand the worker every pending command, plus any lost claim, and mark them."""
    now = utcnow()
    rows = (
        (
            await db.execute(
                select(AggregatorWorkerCommand)
                .where(
                    AggregatorWorkerCommand.channel == channel,
                    or_(
                        AggregatorWorkerCommand.status == "pending",
                        (AggregatorWorkerCommand.status == "claimed")
                        & (AggregatorWorkerCommand.claimed_at < now - CLAIM_TIMEOUT),
                    ),
                )
                .order_by(AggregatorWorkerCommand.created_at)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    # Two commands for one branch (a lost claim and a newer ask): the newer one
    # is the truth, the older is superseded rather than run first and undone.
    newest: dict[tuple[str, uuid.UUID], AggregatorWorkerCommand] = {}
    for row in rows:
        key = (row.kind, row.branch_id)
        prior = newest.get(key)
        if prior is None or row.updated_at >= prior.updated_at:
            if prior is not None:
                _finish(prior, ok=False, error="superseded by a newer request")
            newest[key] = row
        else:
            _finish(row, ok=False, error="superseded by a newer request")
    for row in newest.values():
        row.status = "claimed"
        row.claimed_at = now
        row.updated_at = now
    await db.flush()
    return list(newest.values())


def _finish(
    row: AggregatorWorkerCommand,
    *,
    ok: bool,
    error: str | None = None,
    result: dict | None = None,
) -> None:
    now = utcnow()
    row.status = "done" if ok else "failed"
    row.error = (error or None) and error[:2000]
    row.result = result
    row.finished_at = now
    row.updated_at = now


async def complete(
    db: AsyncSession,
    command_id: uuid.UUID,
    *,
    ok: bool,
    error: str | None,
    result: dict | None,
    dry_run: bool = False,
) -> AggregatorWorkerCommand | None:
    """Record the worker's outcome and move the busy chip it was for.

    The chip only moves if busy mode still wants what this command did: a
    close that lands after staff already turned busy off must not mark the
    `open` row done — the reconciler queues the reopen on its next tick.
    """
    row = await db.get(AggregatorWorkerCommand, command_id)
    if row is None:
        return None
    _finish(row, ok=ok, error=error, result=result)
    if row.kind == KEETA_SHOP_STATUS:
        from app.services.aggregators import busy_sync

        await busy_sync.record_worker_outcome(
            db,
            branch_id=row.branch_id,
            channel=row.channel,
            desired=row.desired,
            ok=ok,
            error=error,
            dry_run=dry_run,
        )
    await db.flush()
    return row


async def supersede_open(
    db: AsyncSession, *, channel: str, kind: str, branch_id: uuid.UUID
) -> None:
    """Retire a claimed-but-unfinished command before queueing its replacement."""
    await db.execute(
        update(AggregatorWorkerCommand)
        .where(
            AggregatorWorkerCommand.channel == channel,
            AggregatorWorkerCommand.kind == kind,
            AggregatorWorkerCommand.branch_id == branch_id,
            AggregatorWorkerCommand.status == "claimed",
            AggregatorWorkerCommand.claimed_at < utcnow() - CLAIM_TIMEOUT,
        )
        .values(status="failed", error="claim timed out", finished_at=utcnow())
    )


__all__ = [
    "CLAIM_TIMEOUT",
    "KEETA_SHOP_STATUS",
    "claim",
    "complete",
    "queue",
    "supersede_open",
    "waiting",
]
