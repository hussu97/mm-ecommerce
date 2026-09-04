"""
The loop that finishes what checkout starts: arrivals, retries, drivers, expiry.

There is no queue in this stack — no Celery, no cron, nothing that survives the
container. So the API wakes itself: a loop inside the app's own lifespan,
checking once a minute for anything owed something now.

Every uvicorn worker has the loop, so each piece of work must happen once and
only once. Two guards, both cheap:

  * A Postgres **advisory lock**, so only one worker in the whole deployment is
    ever inside a sweep. It is a try-lock, not a wait: a worker that does not get
    it goes back to sleep rather than queueing behind the one that did. Taken
    through `app.core.advisory_lock`, which holds it on a connection of its own —
    a lock taken on a pooled session's connection is released onto whichever
    connection the pool hands back next, and when that is the wrong one the lock
    is stranded and every later sweep quietly does nothing.
  * `SELECT … FOR UPDATE SKIP LOCKED` on the rows themselves, so even if the
    advisory lock were bypassed, no two transactions claim the same order.

A minute of granularity is deliberate. The fees are already paid; landing a
dispatch within sixty seconds is precision nobody notices, and polling faster
would only spend database round-trips.
"""

from __future__ import annotations

import asyncio
import logging

from app.core import advisory_lock
from app.core.database import AsyncSessionFactory
from app.services.couriers import courier_service
from app.services.delivery import arrival_service, driver_routing, driver_tracking
from app.services.payments import payment_service

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Arbitrary but fixed. Postgres advisory locks are a flat namespace of 64-bit
#: integers shared by the whole database, so this needs to be a number nothing
#: else in the app will pick. Kept unchanged from when this loop dispatched
#: batches: it is only a unique number, and changing it across a deploy would let
#: an old worker and a new one sweep at the same instant.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4801

_TICK_SECONDS = 60


async def sweep_once() -> bool:
    """
    One pass: claim the lock, do everything due, let go.

    Returns whether this worker held the lock — False means another worker did,
    which is a normal outcome, not a failure.

    **Four kinds of work, one lock.** Orders due to reach the register, single
    orders whose dispatch retry has fallen due, drivers whose position or
    identity we can only learn by asking, and checkouts nobody ever paid for.
    They are swept together because they are the same question asked of four
    tables — *is anything owed something right now* — and each extra loop would
    need its own advisory lock, its own tick and its own reason to exist.

    The order is the order of their dependencies: an arrival is what books a
    driver for an order, a booking has to exist before anybody drives towards it,
    and an abandoned basket depends on nothing so it is last.

    A failure in any one sweep is logged and swallowed rather than allowed out,
    because the work waiting on the next tick is for orders already paid for and
    boxed.
    """
    async with advisory_lock.held(
        _ADVISORY_LOCK_KEY, name="delivery scheduler"
    ) as mine:
        if not mine:
            return False
        async with AsyncSessionFactory() as session:
            try:
                landed = await arrival_service.sweep(session)
                await session.commit()
                if landed:
                    logger.info(
                        "%s order(s) reached the register: %s",
                        len(landed),
                        ", ".join(landed),
                    )
            except Exception:  # noqa: BLE001 — never at the others' expense
                logger.exception("Arrival sweep failed")
                await session.rollback()
            try:
                retried = await courier_service.retry_failed_dispatches(session)
                await session.commit()
                if retried:
                    logger.info("Retried %s failed dispatch(es)", len(retried))
            except Exception:  # noqa: BLE001
                logger.exception("Retry sweep failed")
                await session.rollback()
            try:
                # A booking has to exist before anybody can be driving towards it.
                # Lalamove reports a driver's position exactly once and never
                # mentions a rider swap, so this is the only thing that keeps
                # either current — see `driver_tracking`.
                tracked = await driver_tracking.refresh_live_drivers(session)
                await session.commit()
                if tracked:
                    logger.info("Refreshed %s live driver(s)", tracked)
                # After the positions, never before: a route computed from last
                # minute's pin is a minute out of date before anybody reads it.
                routed = await driver_routing.refresh_routes(session)
                await session.commit()
                if routed:
                    logger.info("Re-routed %s inbound driver(s)", routed)
            except Exception:  # noqa: BLE001
                logger.exception("Driver sweep failed")
                await session.rollback()
            try:
                # The backstop under `checkout.session.expired`, for the ones of
                # it that never arrived. An order left at `created` is not inert:
                # it holds a redemption of its promo code, a place in the
                # customer's first-orders count, and any stock the checkout took.
                expired = await payment_service.expire_stale_checkouts(session)
                await session.commit()
                if expired:
                    logger.info(
                        "%s abandoned checkout(s) cancelled: %s",
                        len(expired),
                        ", ".join(expired),
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Checkout sweep failed")
                await session.rollback()
            return True


async def run_forever() -> None:
    """
    The loop. Cancelled on shutdown; never allowed to die on an exception.

    A sweep that raises has to be survivable — one bad order must not stop every
    future sweep — so failures are logged and the next tick tries again.
    """
    logger.info("Delivery scheduler started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(_TICK_SECONDS)
            await sweep_once()
        except asyncio.CancelledError:
            logger.info("Delivery scheduler stopping")
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any one failure
            logger.exception("Delivery sweep failed; retrying on the next tick")
