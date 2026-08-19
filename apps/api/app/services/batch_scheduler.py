"""
The thing that actually fires a batch when its window closes.

There is no queue in this stack — no Celery, no cron, nothing that survives the
container. A batch that nobody wakes up to send is a customer waiting on a cake
that was packed hours ago, so the API wakes itself: a loop inside the app's own
lifespan, checking once a minute for runs whose time has come.

That means every uvicorn worker has the loop, and a batch must be booked once
and only once. Two guards, both cheap:

  * A Postgres **advisory lock**, so only one worker in the whole deployment is
    ever inside a sweep. It is a try-lock, not a wait: a worker that does not
    get it goes back to sleep rather than queueing up behind the one that did.
    Taken through `app.core.advisory_lock`, which holds it on a connection of
    its own — a lock taken on a pooled session's connection is released onto
    whichever connection the pool hands back next, and when that is the wrong
    one the lock is stranded and every later sweep quietly does nothing.
  * `SELECT … FOR UPDATE SKIP LOCKED` on the batches themselves, so even if the
    advisory lock were somehow bypassed, no two transactions can claim the same
    run.

A worker that loses the race says nothing, because on a healthy deployment
there is nothing to say. `_warn_if_overdue` is the exception, and exists because
silence was indistinguishable from a stranded lock for as long as it took
somebody to notice a cake nobody had collected.

A minute of granularity is deliberate. Windows are hours long and the fee is
already paid; landing a dispatch within sixty seconds of the hour is precision
nobody will notice, and polling faster would only spend database round-trips.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core import advisory_lock
from app.core.database import AsyncSessionFactory
from app.models.delivery_batch import BatchStatusEnum, DeliveryBatch
from app.services import batching_service

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Arbitrary but fixed. Postgres advisory locks are a flat namespace of 64-bit
#: integers shared by the whole database, so this needs to be a number nothing
#: else in the app will pick.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4801  # "mmBATCH" + 1

_TICK_SECONDS = 60

#: How late a run has to be before a worker that is not sweeping says so. Two
#: ticks of slack, so a sweep that is simply in progress is never mistaken for
#: one that has stopped happening.
_OVERDUE_GRACE = timedelta(seconds=_TICK_SECONDS * 2)


async def sweep_once() -> list:
    """
    One pass: claim the lock, send anything due, let go.

    Returns the batches dispatched, or nothing at all if another worker held
    the lock — which is a normal outcome, not a failure.

    **Two kinds of work, one lock.** Runs whose window has closed, and single
    orders whose retry has fallen due. They are swept together because they are
    the same question asked of two tables — *is anything owed a driver right
    now* — and because a second loop would need a second advisory lock, a second
    tick and its own reason to exist. Retries run after batches: a batch going
    out may itself be what clears an order's failure, and doing them in this
    order means that order is already booked by the time its rung comes up.

    Retries cannot stop batches. A failure in the retry sweep is logged and
    swallowed here rather than allowed out, because the runs waiting on the next
    tick are carrying cakes that are already paid for and boxed.
    """
    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="batch dispatcher") as mine:
        if not mine:
            await _warn_if_overdue()
            return []
        async with AsyncSessionFactory() as session:
            dispatched = await batching_service.dispatch_due_batches(session)
            await session.commit()
            try:
                retried = await batching_service.retry_failed_dispatches(session)
                await session.commit()
                if retried:
                    logger.info("Retried %s failed dispatch(es)", len(retried))
            except Exception:  # noqa: BLE001 — never at the batches' expense
                logger.exception("Retry sweep failed; batches were unaffected")
                await session.rollback()
            return dispatched


async def _warn_if_overdue() -> None:
    """
    Say something when the sweep is not happening.

    A worker that does not get the lock has always returned in silence, on the
    reasoning that another worker has the job. That reasoning holds right up
    until nobody has the job — a lock stranded on a pooled connection reads
    exactly like a busy colleague, and the loop ticks on saying nothing while
    packed cakes sit waiting for a van.

    So the losing worker checks the one fact that distinguishes the two: is
    there a run whose time came and went and which is still sitting there. On a
    healthy deployment the worker holding the lock has already sent it and this
    finds nothing, whatever number of workers are running. It needs no lock of
    its own — it is one indexed read of a table this loop already owns.
    """
    try:
        async with AsyncSessionFactory() as session:
            overdue = await session.scalar(
                select(func.count())
                .select_from(DeliveryBatch)
                .where(
                    DeliveryBatch.status == BatchStatusEnum.PENDING.value,
                    DeliveryBatch.dispatch_at
                    < datetime.now(timezone.utc) - _OVERDUE_GRACE,
                )
            )
    except Exception:  # noqa: BLE001 — a watch must not break the loop it watches
        logger.exception("Could not check for overdue runs")
        return

    if overdue:
        logger.warning(
            "%s run(s) are past their departure and nothing is sending them; "
            "the batch dispatcher's advisory lock may be stranded",
            overdue,
        )


async def run_forever() -> None:
    """
    The loop. Cancelled on shutdown; never allowed to die on an exception.

    A sweep that raises has to be survivable — one malformed batch must not
    stop every future batch from going out — so failures are logged and the
    next tick tries again.
    """
    logger.info("Batch dispatcher started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(_TICK_SECONDS)
            dispatched = await sweep_once()
            if dispatched:
                logger.info("Batch dispatcher sent %s run(s)", len(dispatched))
        except asyncio.CancelledError:
            logger.info("Batch dispatcher stopping")
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any one failure
            logger.exception("Batch sweep failed; retrying on the next tick")
