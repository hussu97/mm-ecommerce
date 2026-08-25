"""Mirror an MM aggregator-order move out to Foodics.

This is the write-back that replaced GrubOps' `order-force-*` overrides. An
aggregator order is read in through the GrubOps ingest loop (which also learns
and caches the order's Foodics id on `grubops_order_map.foodics_order_id`); when
MM then moves that order, we drive the *Foodics* order through the actions its
public API actually exposes:

* **packed → dispatch.** The shop pressing Packed marks the Foodics order
  "ready to deliver" (`delivery_status = 2`). GrubTech cascades that to the
  aggregator rider through the normal flow — the step force-complete used to fake
  — verified live on Noon order 4961 (2026-08-25).
* **delivered → finalise.** Five minutes later the auto-close move
  (`packed → delivered`, source `system`) marks the Foodics order delivered
  (`delivery_status = 5`). Foodics has no public "close order" (status 4) write —
  that is a POS-side transition, and GrubTech has usually completed its own side
  at dispatch anyway — so `delivered` on the delivery axis is how we finalise.
* **cancelled → decline.** A cancel declines the order (`status = 3`) while it is
  still `1:Pending`; once Foodics has accepted it there is no public void, so
  those are recorded rather than forced.

Same shape as the GrubOps write-back it replaces — `mirror_status_out` +
`push_status_out_in_background` + `sweep_pending_pushouts`, keyed off the map
row's `last_pushed_status`/`last_push_error` — so `order_lifecycle` calls it the
same way and the ingest loop retries it the same way. `push`/mirror is a no-op
unless `FOODICS_ORDER_PUSH_ENABLED` and a token are set, and only fires for a move
made by *our* side (never one the ingest loop attributed `aggregator`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.models.grubops_order import GrubOpsOrderMap
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import OrderStatusEvent, StatusSourceEnum
from app.models.pos_order import OrderSourceEnum
from app.services.providers.foodics_provider import (
    DELIVERY_DELIVERED,
    DELIVERY_READY,
    STATUS_PENDING,
    FoodicsError,
    provider,
)

logger = logging.getLogger(__name__)

__all__ = [
    "is_enabled",
    "mirror_status_out",
    "push_status_out_in_background",
    "sweep_pending_pushouts",
]

#: The MM statuses that mean something to Foodics, and what each drives. Anything
#: else (created, confirmed, arrived_at_pos, refunded…) has nothing to push.
_MIRRORED = frozenset(
    {OrderStatusEnum.PACKED, OrderStatusEnum.CANCELLED, OrderStatusEnum.DELIVERED}
)

#: Fire-and-forget push tasks, held so the loop's GC does not cancel them
#: mid-flight — the same idiom as the GrubOps/indexnow services.
_pending: set[asyncio.Task] = set()


def is_enabled() -> bool:
    return settings.FOODICS_ORDER_PUSH_ENABLED and provider.is_configured


async def mirror_status_out(
    db, *, mm_order_id, new_status: OrderStatusEnum, actor: str
) -> None:
    """Reflect an MM packed/delivered/cancelled onto the Foodics order.

    Reads the Foodics order first and acts on its live state — dispatch only what
    is not already dispatched, finalise only what is not already delivered,
    decline only what is still pending — so a retry is safe and a state Foodics
    has moved past is recorded rather than fought. The map row's
    `last_pushed_status`/`last_push_error` carry the outcome.
    """
    if new_status not in _MIRRORED:
        return

    order_map = (
        await db.execute(
            select(GrubOpsOrderMap).where(GrubOpsOrderMap.mm_order_id == mm_order_id)
        )
    ).scalar_one_or_none()
    if order_map is None:
        return
    foodics_order_id = order_map.foodics_order_id
    if not foodics_order_id:
        # The publish event that carries the Foodics id lands a beat after the
        # order is created; until the ingest loop has seen it there is nothing to
        # address. Recorded, and retried by `sweep_pending_pushouts` once it fills.
        order_map.last_push_error = "no Foodics order id yet"
        await db.flush()
        return

    try:
        order = await provider.get_order(foodics_order_id)
        if order is None:
            order_map.last_push_error = "Foodics no longer has this order"
            await db.flush()
            return

        status = order.get("status")
        delivery_status = order.get("delivery_status")

        if new_status == OrderStatusEnum.PACKED:
            # Ready-to-deliver, unless already at or past it.
            if delivery_status is None or delivery_status < DELIVERY_READY:
                await provider.update_delivery_status(foodics_order_id, DELIVERY_READY)
        elif new_status == OrderStatusEnum.DELIVERED:
            if delivery_status != DELIVERY_DELIVERED:
                await provider.update_delivery_status(
                    foodics_order_id, DELIVERY_DELIVERED
                )
        elif new_status == OrderStatusEnum.CANCELLED:
            if status == STATUS_PENDING:
                await provider.decline_order(foodics_order_id)
            else:
                # Accepted already — Foodics exposes no public void. Record it so
                # a person can cancel it in the console, and do not claim success.
                order_map.last_push_error = (
                    f"cannot cancel: Foodics order status is {status}, "
                    "no public void once accepted"
                )
                await db.flush()
                return

        order_map.last_pushed_status = new_status.value
        order_map.last_push_error = None
    except FoodicsError as exc:
        order_map.last_push_error = str(exc)[:500]
    await db.flush()


def push_status_out_in_background(
    *, mm_order_id, new_status: OrderStatusEnum, actor: str
) -> None:
    """Fire-and-forget mirror-out. Never raises, no-op when disabled or when no
    event loop is running (tests, the register's sync paths — the ingest tick's
    `sweep_pending_pushouts` is the safety net for the latter)."""
    if not is_enabled():
        return

    async def _run() -> None:
        from app.core.database import AsyncSessionFactory

        try:
            async with AsyncSessionFactory() as db:
                await mirror_status_out(
                    db,
                    mm_order_id=mm_order_id,
                    new_status=new_status,
                    actor=actor,
                )
                await db.commit()
        except Exception:  # noqa: BLE001 — best-effort, the loop reconciles
            logger.exception("Foodics mirror-out failed for order %s", mm_order_id)

    try:
        task = asyncio.create_task(_run())
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        # No running loop — nothing to mirror from here.
        pass


# ── retry: re-fire a mirror-out the immediate push never landed ───────────────

#: How far back a stuck order stays worth retrying. Past a transient Foodics
#: outage or a Foodics id that had not yet been ingested, without probing forever.
_PUSH_RETRY_LOOKBACK_SECONDS = 3600
#: One sweep's worth. Aggregator volume is low; this is a backstop, not a batch.
_PUSH_RETRY_LIMIT = 100


async def sweep_pending_pushouts(db, *, actor: str = "reconcile") -> int:
    """Re-fire any packed/delivered/cancelled mirror-out that never landed.

    `push_status_out_in_background` is best-effort: a token blip, a Foodics id not
    yet ingested at pack time, or a task dropped on restart leaves the Foodics
    order a step behind, its gap recorded on the map row. Each ingest tick, any
    aggregator order still in a mirror-worthy state whose map row has not recorded
    a successful push *of that state* gets one more go through `mirror_status_out`.

    Two guards keep it honest, mirroring the lifecycle's own rule: only orders
    whose current state was set by a non-aggregator actor (so a GrubOps-originated
    move ingested onto our side is never echoed back out), and only those that
    reached the state within the lookback (so a permanently-stuck order is not
    retried forever). Returns how many pushes landed this pass.
    """
    if not is_enabled():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=_PUSH_RETRY_LOOKBACK_SECONDS
    )

    # The most recent status event per order — its `at` is "how long has it sat
    # here" and its `source` is "who put it here". Read from the events, the way
    # the GrubOps sweeps do; there is no `*_at` column.
    latest = (
        select(
            OrderStatusEvent.order_id.label("order_id"),
            OrderStatusEvent.source.label("source"),
            OrderStatusEvent.at.label("at"),
        )
        .order_by(OrderStatusEvent.order_id, OrderStatusEvent.at.desc())
        .distinct(OrderStatusEvent.order_id)
        .subquery()
    )

    rows = (
        await db.execute(
            select(Order, GrubOpsOrderMap)
            .join(GrubOpsOrderMap, GrubOpsOrderMap.mm_order_id == Order.id)
            .join(latest, latest.c.order_id == Order.id)
            .where(
                Order.source == OrderSourceEnum.AGGREGATOR.value,
                Order.status.in_(list(_MIRRORED)),
                GrubOpsOrderMap.foodics_order_id.isnot(None),
                latest.c.source != StatusSourceEnum.AGGREGATOR.value,
                latest.c.at >= cutoff,
            )
            .order_by(latest.c.at)
            .limit(_PUSH_RETRY_LIMIT)
        )
    ).all()

    pushed = 0
    for order, order_map in rows:
        # Already mirrored out for the state it is in — the common case, skipped
        # in memory with no Foodics call.
        if order_map.last_pushed_status == order.status.value:
            continue
        await mirror_status_out(
            db, mm_order_id=order.id, new_status=order.status, actor=actor
        )
        if order_map.last_pushed_status == order.status.value:
            pushed += 1
    return pushed
