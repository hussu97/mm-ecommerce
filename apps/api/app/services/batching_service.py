"""
Sending several orders out on one run instead of several.

A single courier run from the Sharjah kitchen costs AED 28 locally and AED 47
to Dubai. At an average order of AED 61.88 that is most of the contribution
gone, and on Dubai it is more than all of it. The same route carrying five
drops costs AED 12.67 and AED 19.53 per delivery; at ten it is AED 9.83 and
AED 15.70. Nothing else available to us moves the number that far — not a
higher fee, not a bigger basket — and it needs no new capability from the
courier. It is one quotation with fifteen stops instead of fifteen quotations.

The cost of it is time. An order waits until its window closes, and the shop
decides how long that is: long in the morning when orders trickle, an hour at
a time through the evening when 58% of them arrive.

**The rule.** A window is matched against the moment an order becomes
*dispatchable* — when it is packed — not when it was placed. A batch can only
carry what has actually been baked, so scheduling by placement time would build
runs around boxes that do not exist yet. In the ordinary case, where a cake is
packed shortly after it is ordered, the two are the same window anyway.

**When nothing matches.** An order packed in a gap between windows goes on its
own, immediately. A schedule with holes in it is a slower dispatch, never a
stuck one.

**When the schedule changes.** Everything still waiting is re-derived against
the new windows from the moment it became dispatchable. An order whose new
window has already passed goes out on its own rather than waiting for tomorrow.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.delivery_batch import (
    DELIVERY_TIMEZONE,
    MAX_DROPS_PER_ORDER,
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchWindow,
)
from app.models.delivery_polygon import DeliveryPolygon, FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import (
    CourierStatusEnum,
    OrderDelivery,
    is_failed,
)
from app.services import courier_service, lalamove_service
from app.services.providers.lalamove_provider import LalamoveError, provider

logger = logging.getLogger(__name__)

TZ = ZoneInfo(DELIVERY_TIMEZONE)

__all__ = [
    "WindowMatch",
    "assign_or_dispatch",
    "cancel_assignment",
    "dispatch_batch",
    "dispatch_due_batches",
    "find_window",
    "next_dispatch_at",
    "overlapping",
    "reschedule_polygon",
]


# ── window arithmetic ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WindowMatch:
    window: DeliveryBatchWindow
    #: The real moment this window closes, for the day the order fell in.
    dispatch_at: datetime


def _local(moment: datetime) -> datetime:
    """The same instant, read on the shop's clock."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TZ)


def _at(day: date, minutes: int) -> datetime:
    """
    A minute-of-day on a given local date, as a real instant.

    Minute 1440 is midnight closing the day, which is 00:00 the next morning —
    written that way rather than as 23:59 so a 23:00–24:00 window dispatches at
    midnight exactly, not a minute early.
    """
    return datetime.combine(
        day + timedelta(days=minutes // 1440),
        time(hour=(minutes % 1440) // 60, minute=minutes % 60),
        tzinfo=TZ,
    )


def find_window(
    windows: list[DeliveryBatchWindow], moment: datetime
) -> WindowMatch | None:
    """
    The window this instant falls in, and when that window closes.

    Half-open on both ends: 12:00 belongs to the 12:00–18:00 window, not to the
    00:00–12:00 one that just closed. Without that, an order landing exactly on
    a boundary would be scheduled into a batch already leaving.
    """
    local = _local(moment)
    minute_of_day = local.hour * 60 + local.minute

    for window in windows:
        if not window.is_active or not window.contains_minute(minute_of_day):
            continue
        if window.wraps_midnight and minute_of_day >= window.start_minutes:
            # Started today, closes tomorrow.
            dispatch_at = _at(local.date() + timedelta(days=1), window.end_minutes)
        else:
            dispatch_at = _at(local.date(), window.end_minutes)
        return WindowMatch(window=window, dispatch_at=dispatch_at)
    return None


def next_dispatch_at(window: DeliveryBatchWindow, moment: datetime) -> datetime:
    """When `window` next closes, at or after `moment`."""
    local = _local(moment)
    candidate = _at(local.date(), window.end_minutes)
    if candidate < local:
        candidate = _at(local.date() + timedelta(days=1), window.end_minutes)
    return candidate


def overlapping(
    windows: list[DeliveryBatchWindow],
) -> tuple[DeliveryBatchWindow, DeliveryBatchWindow] | None:
    """
    The first pair of active windows that both claim the same minute.

    Overlap is refused rather than resolved. Two windows covering 18:30 makes
    "which batch is this order on" a matter of row order, which is not an answer
    anyone can act on when a driver is late.
    """
    active = [w for w in windows if w.is_active]
    for index, first in enumerate(active):
        for second in active[index + 1 :]:
            if _segments_overlap(first, second):
                return first, second
    return None


def _spans(window: DeliveryBatchWindow) -> list[tuple[int, int]]:
    """Half-open minute ranges, splitting a window that runs past midnight."""
    start, end = window.start_minutes, window.end_minutes
    if window.wraps_midnight:
        return [(start, 1440), (0, end)] if end else [(start, 1440)]
    return [(start, end)]


def _segments_overlap(a: DeliveryBatchWindow, b: DeliveryBatchWindow) -> bool:
    return any(
        a_start < b_end and b_start < a_end
        for a_start, a_end in _spans(a)
        for b_start, b_end in _spans(b)
    )


# ── assignment ────────────────────────────────────────────────────────────────


async def _active_windows(
    db: AsyncSession, polygon_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    result = await db.execute(
        select(DeliveryBatchWindow)
        .where(
            DeliveryBatchWindow.polygon_id == polygon_id,
            DeliveryBatchWindow.is_active.is_(True),
        )
        .order_by(DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute)
    )
    return list(result.scalars().all())


async def _open_batch(
    db: AsyncSession,
    polygon_id: uuid.UUID,
    match: WindowMatch,
) -> DeliveryBatch:
    """The run this order joins, created if it is the first one in the slot."""
    result = await db.execute(
        select(DeliveryBatch).where(
            DeliveryBatch.polygon_id == polygon_id,
            DeliveryBatch.window_id == match.window.id,
            DeliveryBatch.dispatch_at == match.dispatch_at,
            DeliveryBatch.status == BatchStatusEnum.PENDING.value,
        )
    )
    batch = result.scalars().first()
    if batch is not None:
        return batch

    batch = DeliveryBatch(
        polygon_id=polygon_id,
        window_id=match.window.id,
        window_label=match.window.label,
        dispatch_at=match.dispatch_at,
        status=BatchStatusEnum.PENDING.value,
    )
    db.add(batch)
    await db.flush()
    return batch


async def assign_or_dispatch(
    db: AsyncSession, order: Order, *, moment: datetime | None = None
) -> OrderDelivery | None:
    """
    The single entry point when an order becomes ready to leave.

    Either it joins a run that has not left yet, or it goes on its own right
    now. Third-party zones fall straight through and keep the manual flow they
    have always had.

    Only Lalamove has runs to share. A multi-drop Lalamove order is one booking
    with fifteen stops; noon Send's equivalent is a different product with a
    different endpoint and a cap of three, so a `noon_send` zone dispatches each
    order on its own and joins no batch.
    """
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        return None
    if not courier_service.books_itself(delivery.provider):
        return delivery
    if delivery.courier_order_id and not is_failed(
        delivery.provider, delivery.courier_status
    ):
        return delivery
    if delivery.batch_id and delivery.batch is not None and delivery.batch.is_open:
        # Already waiting on a run. Nothing to decide.
        return delivery

    now = moment or datetime.now(timezone.utc)
    delivery.dispatchable_at = now

    if delivery.provider != FulfilmentProviderEnum.LALAMOVE.value:
        return await courier_service.dispatch(db, order)

    if not lalamove_service.is_enabled():
        # No courier configured, so there is no shared run to wait for. Falling
        # through to the single-order path records "dispatch this by hand" on
        # the order immediately, where the person packing it will see it —
        # rather than parking it in a batch that can only fail when its window
        # closes an hour later.
        return await courier_service.dispatch(db, order)

    if delivery.polygon_id is None:
        # An order placed before zones carried an id, or against a map that has
        # since been deleted. It still has to go out; it just goes alone.
        return await courier_service.dispatch(db, order)

    windows = await _active_windows(db, delivery.polygon_id)
    match = find_window(windows, now)
    if match is None:
        logger.info(
            "No batch window covers %s for order %s; dispatching on its own",
            _local(now).strftime("%H:%M"),
            order.order_number,
        )
        return await courier_service.dispatch(db, order)

    batch = await _open_batch(db, delivery.polygon_id, match)
    delivery.batch_id = batch.id
    delivery.last_error = None
    batch.stop_count = await _count_deliveries(db, batch.id)
    logger.info(
        "Order %s joins %s, leaving %s",
        order.order_number,
        batch.window_label,
        batch.dispatch_at.isoformat(),
    )
    return delivery


async def cancel_assignment(db: AsyncSession, delivery: OrderDelivery) -> None:
    """Take an order off a run that has not left. Empties the run if it was the last."""
    if not delivery.batch_id:
        return
    batch = await db.get(DeliveryBatch, delivery.batch_id)
    delivery.batch_id = None
    if batch is None or not batch.is_open:
        return
    remaining = await _count_deliveries(db, batch.id)
    batch.stop_count = remaining
    if remaining == 0:
        batch.status = BatchStatusEnum.CANCELLED.value


async def _count_deliveries(db: AsyncSession, batch_id: uuid.UUID) -> int:
    result = await db.execute(
        select(OrderDelivery.id).where(OrderDelivery.batch_id == batch_id)
    )
    return len(result.scalars().all())


# ── rescheduling ──────────────────────────────────────────────────────────────


async def reschedule_polygon(db: AsyncSession, polygon_id: uuid.UUID) -> int:
    """
    Re-derive every waiting assignment in this zone against the current windows.

    Called after the schedule is edited. An order whose window moved lands on
    the new one; an order whose window disappeared, or whose new window has
    already closed, goes out on its own instead of waiting for a slot that will
    not come round until tomorrow.

    Returns how many assignments changed.
    """
    windows = await _active_windows(db, polygon_id)
    now = datetime.now(timezone.utc)

    waiting = (
        (
            await db.execute(
                select(OrderDelivery)
                .join(DeliveryBatch, DeliveryBatch.id == OrderDelivery.batch_id)
                .where(
                    DeliveryBatch.polygon_id == polygon_id,
                    DeliveryBatch.status == BatchStatusEnum.PENDING.value,
                    OrderDelivery.courier_order_id.is_(None),
                )
                .options(selectinload(OrderDelivery.order))
            )
        )
        .scalars()
        .all()
    )

    moved = 0
    strays: list[OrderDelivery] = []
    for delivery in waiting:
        match = find_window(windows, delivery.dispatchable_at or now)
        if match is not None and match.dispatch_at <= now:
            # The new slot is already over — treat it as no slot at all rather
            # than dispatching into the past.
            match = None
        if match is None:
            await cancel_assignment(db, delivery)
            strays.append(delivery)
            moved += 1
            continue

        batch = await _open_batch(db, polygon_id, match)
        if batch.id == delivery.batch_id:
            continue
        await cancel_assignment(db, delivery)
        delivery.batch_id = batch.id
        moved += 1

    await db.flush()
    for batch_id in {b for b in (d.batch_id for d in waiting) if b}:
        batch = await db.get(DeliveryBatch, batch_id)
        if batch is not None and batch.is_open:
            batch.stop_count = await _count_deliveries(db, batch_id)

    for delivery in strays:
        if delivery.order is not None:
            await courier_service.dispatch(db, delivery.order)

    if moved:
        logger.info("Rescheduled %s waiting orders in zone %s", moved, polygon_id)
    return moved


# ── dispatch ──────────────────────────────────────────────────────────────────


async def dispatch_due_batches(db: AsyncSession, *, limit: int = 20) -> list[uuid.UUID]:
    """
    Send every run whose window has closed.

    Claiming is a status flip inside the row lock, so two sweeps running at once
    cannot both book the same batch. The lock is skipped rather than waited on:
    a batch another worker is already holding is not this worker's problem.
    """
    now = datetime.now(timezone.utc)
    due = (
        (
            await db.execute(
                select(DeliveryBatch)
                .where(
                    DeliveryBatch.status == BatchStatusEnum.PENDING.value,
                    DeliveryBatch.dispatch_at <= now,
                )
                .order_by(DeliveryBatch.dispatch_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    dispatched: list[uuid.UUID] = []
    for batch in due:
        batch.status = BatchStatusEnum.DISPATCHING.value
        await db.commit()
        try:
            await dispatch_batch(db, batch)
        except Exception:  # pragma: no cover — defensive
            logger.exception("Batch %s blew up while dispatching", batch.id)
            batch.status = BatchStatusEnum.FAILED.value
            batch.last_error = "Dispatch failed unexpectedly"
        await db.commit()
        dispatched.append(batch.id)
    return dispatched


async def dispatch_batch(db: AsyncSession, batch: DeliveryBatch) -> DeliveryBatch:
    """
    Book one run, route-optimised, and record what came back on every order in it.

    Route optimisation is free and reduces the price — a ten-drop Sharjah route
    quoted AED 107 unordered against AED 99 optimised — so it is always on.

    Fifteen drops is the courier's ceiling for one order. A fuller batch is
    split across several, which still beats sending them singly: the second
    courier order carries its own drops at the same marginal rate.
    """
    deliveries = await _ready_deliveries(db, batch.id)
    if not deliveries:
        batch.status = BatchStatusEnum.CANCELLED.value
        batch.stop_count = 0
        logger.info("Batch %s had nothing ready to send", batch.id)
        return batch

    if not lalamove_service.is_enabled():
        batch.status = BatchStatusEnum.FAILED.value
        batch.last_error = "Courier is not configured; dispatch these orders by hand"
        return batch

    pickup = await lalamove_service.resolve_pickup(db)
    if pickup is None:
        batch.status = BatchStatusEnum.FAILED.value
        batch.last_error = "No pickup branch is configured"
        return batch

    drops: list[tuple[OrderDelivery, lalamove_service.Drop]] = []
    for delivery in deliveries:
        drop, reason = lalamove_service.build_drop(delivery.order)
        if drop is None:
            # One bad address must not strand the other fourteen. It comes off
            # the run and shows up as an order needing a human.
            delivery.last_error = reason
            delivery.batch_id = None
            continue
        drops.append((delivery, drop))

    if not drops:
        batch.status = BatchStatusEnum.FAILED.value
        batch.last_error = "No order in this run had a usable address"
        batch.stop_count = 0
        return batch

    chunks = [
        drops[i : i + MAX_DROPS_PER_ORDER]
        for i in range(0, len(drops), MAX_DROPS_PER_ORDER)
    ]
    booked = 0
    errors: list[str] = []
    for index, chunk in enumerate(chunks):
        try:
            await _book_chunk(db, batch, pickup, chunk, part=index, parts=len(chunks))
            booked += len(chunk)
        except LalamoveError as exc:
            errors.append(str(exc))
            for delivery, _ in chunk:
                delivery.last_error = str(exc)
            logger.warning("Batch %s part %s failed: %s", batch.id, index + 1, exc)

    batch.stop_count = booked
    if booked == 0:
        batch.status = BatchStatusEnum.FAILED.value
        batch.last_error = errors[0] if errors else "Courier refused the run"
        return batch

    batch.status = BatchStatusEnum.DISPATCHED.value
    batch.dispatched_at = datetime.now(timezone.utc)
    batch.last_error = "; ".join(errors) or None
    per_delivery = batch.cost_per_delivery
    logger.info(
        "Batch %s dispatched: %s drops, %s %s total%s",
        batch.window_label or batch.id,
        booked,
        batch.cost_currency or "",
        batch.cost_total if batch.cost_total is not None else "-",
        f" ({per_delivery} each)" if per_delivery is not None else "",
    )
    return batch


async def _book_chunk(
    db: AsyncSession,
    batch: DeliveryBatch,
    pickup: lalamove_service.PickupPoint,
    chunk: list[tuple[OrderDelivery, lalamove_service.Drop]],
    *,
    part: int,
    parts: int,
) -> None:
    quotation = await provider.create_quotation(
        [pickup.as_stop(), *(drop.stop for _, drop in chunk)],
        special_requests=lalamove_service.special_requests(),
        # Free, and it reorders the drops into the cheapest sequence. The order
        # of `stops` in the reply is the route, which is how each customer's
        # position is known.
        is_route_optimized=len(chunk) > 1,
    )
    stops = quotation.get("stops") or []
    if len(stops) != len(chunk) + 1:
        raise LalamoveError(
            f"Courier returned {len(stops)} stops for {len(chunk) + 1} we sent"
        )

    # Optimisation reorders the drops, so the reply is matched back by
    # coordinates rather than by position — otherwise every customer after the
    # first would be booked against somebody else's stop.
    by_point = {_point_key(drop.stop): (delivery, drop) for delivery, drop in chunk}
    recipients: list[dict] = []
    routed: list[tuple[OrderDelivery, str | None, int]] = []
    for sequence, stop in enumerate(stops[1:], start=1):
        entry = by_point.pop(_point_key(stop), None)
        if entry is None:
            raise LalamoveError("Courier returned a stop we did not send")
        delivery, drop = entry
        stop_id = stop.get("stopId")
        recipients.append(drop.recipient(stop_id))
        routed.append((delivery, stop_id, sequence))

    booking = await provider.place_order(
        quotation_id=quotation.get("quotationId", ""),
        sender={
            "stopId": stops[0].get("stopId"),
            "name": pickup.name,
            "phone": pickup.phone,
        },
        recipients=recipients,
        is_pod_enabled=True,
        metadata={
            "batch_id": str(batch.id),
            "order_numbers": ",".join(d.order_number for _, d in chunk)[:255],
        },
    )

    courier_order_id = booking.get("orderId")
    estimate = lalamove_service.parse_quotation(quotation)
    now = datetime.now(timezone.utc)
    status = booking.get("status") or CourierStatusEnum.ASSIGNING_DRIVER.value

    # The first part owns the batch-level fields; later parts add their cost so
    # the total is what the whole run cost, not what one third of it did.
    if part == 0:
        batch.courier_order_id = courier_order_id
        batch.quotation_id = quotation.get("quotationId")
        batch.share_link = booking.get("shareLink")
        batch.courier_status = status
        batch.driver_id = booking.get("driverId") or None
        batch.distance_m = estimate.distance_m if estimate else None
        batch.cost_currency = estimate.currency if estimate else None
        batch.cost_total = estimate.cost if estimate else None
        batch.price_breakdown = booking.get("priceBreakdown")
        batch.last_payload = booking
    else:
        if estimate is not None:
            batch.cost_total = (batch.cost_total or Decimal("0")) + estimate.cost
            batch.distance_m = (batch.distance_m or 0) + (estimate.distance_m or 0)

    share_of_cost = (
        (estimate.cost / len(chunk)).quantize(Decimal("0.01"))
        if estimate is not None and chunk
        else None
    )

    for delivery, stop_id, sequence in routed:
        if delivery.courier_order_id:
            delivery.previous_courier_order_ids = [
                *(delivery.previous_courier_order_ids or []),
                delivery.courier_order_id,
            ]
        delivery.courier_order_id = courier_order_id
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_status = status
        delivery.share_link = booking.get("shareLink")
        delivery.driver_id = booking.get("driverId") or None
        delivery.stop_id = stop_id
        delivery.stop_sequence = sequence
        delivery.booked_at = now
        delivery.status_updated_at = now
        delivery.quotation_id = quotation.get("quotationId")
        delivery.quoted_at = now
        # An even split of one shared journey. Nobody's drop has a price of its
        # own, and the average is the honest way to charge the run against the
        # orders that caused it.
        if share_of_cost is not None:
            delivery.cost_total = share_of_cost
            delivery.quoted_cost = share_of_cost
            delivery.quoted_currency = estimate.currency if estimate else None
        delivery.last_error = None
        delivery.last_payload = booking

    logger.info(
        "Batch %s part %s/%s booked as %s (%s drops)",
        batch.id,
        part + 1,
        parts,
        courier_order_id,
        len(chunk),
    )


def _point_key(stop: dict) -> tuple[str, str]:
    """
    Round-trip-stable identity for a stop.

    The courier echoes coordinates back as strings and sometimes trims them, so
    they are compared at five decimals — about a metre, far finer than two
    customers can be apart and coarse enough to survive the reformatting.
    """
    coords = stop.get("coordinates") or {}
    return (
        f"{float(coords.get('lat', 0)):.5f}",
        f"{float(coords.get('lng', 0)):.5f}",
    )


async def _ready_deliveries(
    db: AsyncSession, batch_id: uuid.UUID
) -> list[OrderDelivery]:
    """
    The orders in this run that can actually travel.

    An order cancelled or refunded since it joined is dropped, and so is one
    that somehow got booked separately in the meantime. Sending a driver to
    collect a box that no longer exists is worse than a short run.
    """
    result = await db.execute(
        select(OrderDelivery)
        .join(Order, Order.id == OrderDelivery.order_id)
        .where(
            OrderDelivery.batch_id == batch_id,
            OrderDelivery.courier_order_id.is_(None),
            Order.status.notin_(
                [
                    OrderStatusEnum.CANCELLED,
                    OrderStatusEnum.REFUNDED,
                    OrderStatusEnum.DISPUTED,
                ]
            ),
        )
        .options(selectinload(OrderDelivery.order))
        .order_by(OrderDelivery.created_at)
    )
    return list(result.scalars().all())


async def polygon_for(
    db: AsyncSession, polygon_id: uuid.UUID
) -> DeliveryPolygon | None:
    return await db.get(DeliveryPolygon, polygon_id)
