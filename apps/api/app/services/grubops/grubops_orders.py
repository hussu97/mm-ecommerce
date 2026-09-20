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


def _summary_needs_ingest(order_map: GrubOpsOrderMap, summary: dict) -> bool:
    """Whether a summary is new or has advanced beyond our durable ledger."""
    return not (
        order_map.mm_order_id is not None
        and order_map.last_grubops_status == summary.get("status")
    )


def _map_values(summary: dict) -> dict:
    source = summary.get("source") or {}
    return {
        "grubops_order_id": str(summary.get("orderId")),
        "external_id": summary.get("externalId"),
        "source_channel": source.get("channel"),
        "location_id": summary.get("locationId"),
    }


async def _load_order_maps(db, summaries: list[dict]) -> dict[str, GrubOpsOrderMap]:
    """Load the tick's ledger in bulk and durably insert genuinely new ids.

    The normal steady-state pass is one SELECT for every summary, instead of two
    statements per order.  New ledger rows are inserted in one statement and
    committed before detail ingestion, preserving the promise that a failed
    create still records that GrubOps showed us the order.
    """
    values_by_id: dict[str, dict] = {}
    for summary in summaries:
        values = _map_values(summary)
        values_by_id.setdefault(values["grubops_order_id"], values)
    if not values_by_id:
        return {}

    order_ids = list(values_by_id)
    rows = list(
        (
            await db.execute(
                select(GrubOpsOrderMap).where(
                    GrubOpsOrderMap.grubops_order_id.in_(order_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.grubops_order_id: row for row in rows}
    missing = [
        values_by_id[order_id] for order_id in order_ids if order_id not in by_id
    ]
    if not missing:
        return by_id

    await db.execute(
        pg_insert(GrubOpsOrderMap)
        .values(missing)
        .on_conflict_do_nothing(index_elements=["grubops_order_id"])
    )
    # This scheduler owns the session.  Persist the sighting before a later
    # provider/detail failure so the null-mm-order ledger survives for retry.
    await db.commit()
    inserted = list(
        (
            await db.execute(
                select(GrubOpsOrderMap).where(
                    GrubOpsOrderMap.grubops_order_id.in_(
                        [values["grubops_order_id"] for values in missing]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    by_id.update({row.grubops_order_id: row for row in inserted})
    return by_id


async def _ingest_one(db, summary: dict, order_map: GrubOpsOrderMap) -> bool:
    """Fetch and ingest one summary already known to need work."""
    grubops_order_id = str(summary.get("orderId"))

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
        try:
            order_maps = await _load_order_maps(db, summaries)
        except Exception:  # noqa: BLE001 — housekeeping should still run
            logger.exception("GrubOps: could not bulk-load the order ledger")
            await db.rollback()
            order_maps = {}
        for summary in summaries:
            grubops_order_id = str(summary.get("orderId"))
            seen_ids.add(grubops_order_id)
            order_map = order_maps.get(grubops_order_id)
            if order_map is None or not _summary_needs_ingest(order_map, summary):
                continue
            # A SAVEPOINT and a commit per CHANGED order (F-AGG-18). The whole sweep used
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
                    spent = await _ingest_one(db, summary, order_map)
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
