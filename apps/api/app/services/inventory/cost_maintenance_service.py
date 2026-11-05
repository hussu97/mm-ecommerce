"""The costing loops: settle deferred postings, then finish estate-wide.

Two loops, both in the scheduler process:

* **Settle** (:func:`run_settle_forever`, every ~15 s). A posting the costing
  engine's fast path could only cost by re-costing history — a receipt that
  prices waiting stock, a count onto negative stock — is booked at what the
  fast path knows and its warehouse queued (``inventory_costing_settle``)
  instead of replayed inside the till's request, which held a produce or a
  report submit for 5-11 s and produce-all for ~30 s while every other request
  on the worker waited. This loop replays each queued warehouse under its
  branch lock (tried, never waited on), one warehouse per transaction, inside a
  per-tick budget. The provisional window is one tick plus one warehouse replay
  — well under a minute — and each settle logs ``waited_ms`` so it is measured,
  not assumed.
* **Estate** (:func:`run_forever`, every 60 s). A warehouse replay — inline or
  settled — re-costs the warehouse itself, but a price learned there can also
  re-cost stock that has since been **transferred to another branch**, and
  that branch's lock is not the poster's to take. So the warehouse is marked
  (``inventory_costing_dirty``) and this sweep replays the whole estate under
  every branch lock, clearing the marks — and the settle queue with them, which
  makes it the settle loop's backstop: if the settle loop is down, a deferred
  warehouse is still re-costed within this sweep's minute, and in the worst
  case by the nightly replay. That nightly unconditional replay is the safety
  net: the stored projection should already match, and the change count it logs
  is the drift monitor (expected 0).

This replaced the zero-cost *recost* sweep, which posted cost adjustments to
revalue made stock left at zero. Under v3 made stock is costed from the actual
FIFO cost of its ingredients and re-costed when they learn their price, so there
is nothing left for it to patch.

Same lifespan shape as its neighbours: no cron in this stack, an advisory lock so
a second copy across blue/green is harmless, storefront app only. The branch
locks are *tried*, never waited on — a busy branch just defers the replay a tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from app.core import advisory_lock, background, heartbeat
from app.models.base import utcnow
from app.models.inventory import InventoryCostingState
from app.services.inventory import costing_service

logger = logging.getLogger(__name__)

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 0D — the
#: slot the retired recost sweeper held, so blue/green never runs both.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480D

_TICK_SECONDS = 60
_NIGHTLY = timedelta(hours=24)

#: Its own slot ("mmSETTL"), so the settle loop and the estate sweep can each be
#: leader somewhere; the branch locks are what keep them off each other's rows.
_SETTLE_LOCK_KEY = 0x6D6D_5345_5454_4C01
#: How often queued warehouses are replayed. With one warehouse replay at a few
#: seconds, the provisional window is about this plus that.
_SETTLE_TICK_SECONDS = 15
#: Stop starting new warehouse replays after this much of a tick.
_SETTLE_BUDGET_SECONDS = 30
#: Hard ceiling on a tick, replays in flight included: a wedged replay is
#: cancelled (its transaction rolled back, its request kept) and the loop lives.
_SETTLE_TIMEOUT_SECONDS = 90

__all__ = ["run_forever", "run_settle_forever", "settle_once", "sweep_once"]


async def settle_once(db) -> list[costing_service.SettleResult]:
    """One settle tick on the leader's session: replay what the postings queued."""
    return await costing_service.settle_marked(
        db, budget_seconds=_SETTLE_BUDGET_SECONDS
    )


async def run_settle_forever() -> None:
    """The settle loop. Leader-elected, sleeps first, a per-tick budget and timeout.

    Never allowed to die on an exception or a timeout — a request it could not
    settle stays queued, and the estate sweep is the backstop meanwhile.
    """
    logger.info("Costing settle loop started (every %ss)", _SETTLE_TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(background.jittered(_SETTLE_TICK_SECONDS))
            await heartbeat.beat("costing_settle")
            async with advisory_lock.held_session(
                _SETTLE_LOCK_KEY, name="costing settle loop"
            ) as db:
                if db is None:
                    continue
                async with asyncio.timeout(_SETTLE_TIMEOUT_SECONDS):
                    await settle_once(db)
        except asyncio.CancelledError:
            logger.info("Costing settle loop stopping")
            raise
        except (Exception, TimeoutError):  # noqa: BLE001 — the next tick must still run
            logger.exception("Costing settle tick failed")


async def sweep_once(db) -> costing_service.ReplayResult | None:
    """One tick: replay the estate if a warehouse asked, or a day has passed."""
    state = await db.get(InventoryCostingState, True)
    due = (
        state is None
        or state.last_estate_replay_at is None
        or utcnow() - state.last_estate_replay_at >= _NIGHTLY
    )
    if due:
        return await costing_service.replay_estate(db, wait=False)
    return await costing_service.replay_marked_estate(db)


async def run_forever() -> None:
    logger.info("Estate costing sweeper started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("costing_estate_sweeper")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="costing estate sweeper"
            ) as db:
                if db is None:
                    continue
                result = await sweep_once(db)
                await db.commit()
                if result is not None:
                    log = logger.warning if result.changes else logger.info
                    log(
                        "Estate costing replay: %s lines, %s projection changes, %sms",
                        result.lines,
                        result.changes,
                        result.elapsed_ms,
                    )
                # The projection is the whole estate's FIFO state (~140 MB at
                # 43k lines); don't hold it through the minute's sleep.
                result = None
        except asyncio.CancelledError:
            logger.info("Estate costing sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Estate costing sweeper tick failed")
