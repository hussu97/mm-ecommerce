"""The estate costing sweep: finish what a warehouse replay started.

A posting that the costing engine cannot cost on its fast path replays its own
warehouse, under that branch's lock only (`costing_service.cost_posting`). That
is enough for the warehouse itself, but a price learned there can also re-cost
stock that has since been **transferred to another branch** — and that branch's
lock is not the poster's to take. So the poster marks its warehouse
(``inventory_costing_dirty``) and this sweep, once a minute, replays the whole
estate under every branch lock, clearing the marks. A nightly unconditional
replay is the safety net: the stored projection should already match, and the
change count it logs is the drift monitor (expected 0).

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

from app.core import advisory_lock, heartbeat
from app.models.base import utcnow
from app.models.inventory import InventoryCostingState
from app.services.inventory import costing_service

logger = logging.getLogger(__name__)

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 0D — the
#: slot the retired recost sweeper held, so blue/green never runs both.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480D

_TICK_SECONDS = 60
_NIGHTLY = timedelta(hours=24)

__all__ = ["run_forever", "sweep_once"]


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
        except asyncio.CancelledError:
            logger.info("Estate costing sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Estate costing sweeper tick failed")
