"""The loop that pulls aggregator orders out of GrubOps.

GrubTech does not push to us — its own console polls — so this is a poll loop,
not a webhook. Each tick it asks GrubOps for the orders currently in play, and
for any it has not seen or whose status has moved, it fetches the full order and
hands it to `grubops_orders_service.ingest`. That service creates the MM order
the first time (and rings the register), and mirrors GrubOps's status onto ours
thereafter.

Shaped like `grubops_reconcile`: `run_forever`/`sweep_once`, its own advisory
lock so a second worker does nothing rather than double-ingesting, gated on
`GRUBOPS_ORDERS_ENABLED`, started only where the storefront app runs its
background loops. A cheap `getOrderCount` probe keeps a quiet tick close to
free.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import advisory_lock, background, heartbeat
from app.core.config import settings
from app.models.grubops_order import GrubOpsOrderMap
from app.services.foodics import foodics_orders_service
from app.services.grubops import grubops_orders_service
from app.services.providers.grubops_provider import GrubOpsError, provider

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: "mmBATCH" + 4. After delivery_scheduler (…4801), log_retention (…4802) and the
#: GrubOps OOS reconcile (…4803). A new loop needs a number nobody else holds.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4804


def _tick_seconds() -> int:
    return settings.GRUBOPS_ORDERS_TICK_SECONDS


async def _ingest_one(db, summary: dict) -> bool:
    """Upsert the ledger row for one summary, and ingest it if it is new or has
    moved. Returns whether a GrubOps detail fetch was spent."""
    grubops_order_id = str(summary.get("orderId"))
    status = summary.get("status")
    source = summary.get("source") or {}

    # Upsert the map row first, so a create that fails still leaves a record of
    # having seen the order (with a null mm_order_id to retry).
    values = {
        "grubops_order_id": grubops_order_id,
        "external_id": summary.get("externalId"),
        "source_channel": source.get("channel"),
        "location_id": summary.get("locationId"),
    }
    await db.execute(
        pg_insert(GrubOpsOrderMap)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["grubops_order_id"])
    )
    order_map = (
        await db.execute(
            select(GrubOpsOrderMap).where(
                GrubOpsOrderMap.grubops_order_id == grubops_order_id
            )
        )
    ).scalar_one()

    # Nothing to do if we have already ingested this exact status and the order
    # exists — the common case on a busy board.
    if order_map.mm_order_id is not None and order_map.last_grubops_status == status:
        return False

    info = await provider.get_order(grubops_order_id)
    if info is None:
        return True
    await grubops_orders_service.ingest(db, info, order_map)
    return True


async def sweep_once() -> int:
    """
    One poll of GrubOps. Returns how many orders were created or advanced.

    `{}`-equivalent (0) when another worker holds the lock or nothing changed.
    """
    if not grubops_orders_service.is_enabled():
        return 0

    async with advisory_lock.held_session(
        _ADVISORY_LOCK_KEY, name="grubops orders"
    ) as db:
        if db is None:
            return 0

        # One connection, not two: `held_session` binds this session to the lock's
        # own connection, so the ingest work no longer books a second scheduler
        # connection on top of the lock. `list_orders` runs before any DB write
        # with only that one connection held.
        try:
            summaries = await provider.list_orders(
                statuses=grubops_orders_service.LIVE_STATUSES
            )
        except GrubOpsError:
            # A listing failure does not skip the auto-close below: closing a
            # packed order that has passed its window needs no GrubOps call, and
            # is exactly the housekeeping that should still happen while GrubOps
            # is briefly unreachable.
            logger.exception("GrubOps: could not list orders")
            summaries = []

        touched = 0
        seen_ids: set[str] = set()
        for summary in summaries:
            seen_ids.add(str(summary.get("orderId")))
            # A SAVEPOINT and a commit per order (F-AGG-18). The whole sweep used
            # to accumulate every order's ingest in one transaction committed only
            # at the end, so a single order whose write aborted the transaction (a
            # constraint hit, a `get_order` shape the ingest choked on) poisoned it
            # for every later order AND the final commit — one bad order lost the
            # whole tick. The savepoint rolls back just this order and leaves the
            # transaction usable; the commit makes it durable, so nothing after it
            # — another order or the housekeeping below — can undo it. (Committing
            # is safe here: `held_session` keeps the SESSION-level advisory lock on
            # its own connection across every commit.) One malformed payload still
            # does not end the pass.
            try:
                async with db.begin_nested():
                    spent = await _ingest_one(db, summary)
                await db.commit()
                if spent:
                    touched += 1
            except Exception:  # noqa: BLE001 — one bad order must not stop the rest
                logger.exception(
                    "GrubOps: failed to ingest order %s",
                    summary.get("orderId"),
                )
                await db.rollback()
                continue

        # Re-poll open orders the summary window has dropped — an order that
        # lingered at arrived_at_pos and was then cancelled or completed
        # aggregator-side, which the loop above can no longer see. Each
        # housekeeping sweep commits on success and rolls back on failure, on its
        # own, so one sweep's failure cannot poison or discard another's work.
        try:
            repolled = await grubops_orders_service.sweep_open_orders(db, seen_ids)
            await db.commit()
            if repolled:
                logger.info("GrubOps re-polled %s aged-out order(s)", repolled)
                touched += repolled
        except Exception:  # noqa: BLE001 — housekeeping must not end the tick
            logger.exception("GrubOps: open-order re-poll sweep failed")
            await db.rollback()

        # Re-fire any packed/delivered/cancelled push to Foodics the immediate
        # mirror-out never landed — a Foodics id not yet ingested at pack time,
        # a token blip, a task dropped on a restart. Runs each tick here because
        # this is where the Foodics id is learned.
        try:
            repushed = await foodics_orders_service.sweep_pending_pushouts(db)
            await db.commit()
            if repushed:
                logger.info("Foodics re-pushed %s stuck status(es)", repushed)
                touched += repushed
        except Exception:  # noqa: BLE001 — housekeeping must not end the tick
            logger.exception("Foodics: mirror-out retry sweep failed")
            await db.rollback()

        # Close out any packed aggregator order past its window.
        try:
            closed = await grubops_orders_service.sweep_auto_close(db)
            await db.commit()
            if closed:
                logger.info("GrubOps auto-closed %s packed order(s)", closed)
                touched += closed
        except Exception:  # noqa: BLE001 — housekeeping must not end the tick
            logger.exception("GrubOps: auto-close sweep failed")
            await db.rollback()

        return touched


async def run_forever() -> None:
    """
    The loop. Cancelled on shutdown; never allowed to die on an exception.

    A tick that raises must be survivable: the failure mode is an aggregator
    order the kitchen has not been told about, worth retrying on the next tick
    rather than losing until the next deploy.
    """
    logger.info("GrubOps order ingest started (every %ss)", _tick_seconds())
    while True:
        try:
            # Sleeps first: boot is busy, and the first orders can wait a tick.
            await asyncio.sleep(background.jittered(_tick_seconds()))
            await heartbeat.beat("grubops_orders")
            touched = await sweep_once()
            if touched:
                logger.info("GrubOps order ingest handled %s order(s)", touched)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad tick must not stop them all
            logger.exception("GrubOps order ingest tick failed")
