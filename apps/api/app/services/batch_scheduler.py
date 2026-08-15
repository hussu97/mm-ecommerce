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
  * `SELECT … FOR UPDATE SKIP LOCKED` on the batches themselves, so even if the
    advisory lock were somehow bypassed, no two transactions can claim the same
    run.

A minute of granularity is deliberate. Windows are hours long and the fee is
already paid; landing a dispatch within sixty seconds of the hour is precision
nobody will notice, and polling faster would only spend database round-trips.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.core.database import AsyncSessionFactory
from app.services import batching_service

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Arbitrary but fixed. Postgres advisory locks are a flat namespace of 64-bit
#: integers shared by the whole database, so this needs to be a number nothing
#: else in the app will pick.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4801  # "mmBATCH" + 1

_TICK_SECONDS = 60


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
    async with AsyncSessionFactory() as session:
        got_lock = await session.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
        )
        if not got_lock:
            return []
        try:
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
        finally:
            # Released explicitly rather than left to the connection closing,
            # because a pooled connection may not close for a long time and the
            # lock would outlive the work by hours.
            await session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            )
            await session.commit()


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
