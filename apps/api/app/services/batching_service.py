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

**A run is a departure time, not a zone.** Windows are per zone because density
is local, but a van is not. Two zones whose slots close on the same minute share
one run: the orders waiting in both go out on a single courier order, route-
optimised across all of them. Sending two vans from the same kitchen at the same
moment pays the base fare twice for one journey's worth of work.

**A refusal is not the end.** An empty wallet, a courier outage, a reply that
does not parse — none of those mean the cakes are not going out, and none of
them should wait for somebody to notice. A run that fails for a reason another
attempt could fix comes back on a short ladder, 5 then 15 then 45 minutes, and
then stops. A run that fails for a reason another attempt cannot fix — an
address the courier will never accept, no courier configured at all — is left
alone, because retrying identical data gets an identical answer and spends
courier API calls to learn nothing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
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

#: How long to wait after each failed attempt before trying again. The length of
#: this tuple is the number of retries: four attempts in all, spread over about
#: an hour, then the run is left for a human.
#:
#: Short at first because the common failure is momentary — a courier 5xx, a
#: reply that did not parse — and long at the end because the failure that
#: actually needs the time is an empty wallet, which needs somebody to top it up.
RETRY_BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=45),
)

__all__ = [
    "RETRY_BACKOFF",
    "WindowMatch",
    "active_windows",
    "assign_or_dispatch",
    "cancel_assignment",
    "dispatch_batch",
    "dispatch_due_batches",
    "find_window",
    "kitchen_is_open",
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


async def active_windows(
    db: AsyncSession, polygon_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    """This zone's live schedule, earliest first."""
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
    """
    The run this order joins, created if it is the first one leaving then.

    Matched on the departure time alone, not on the zone. Zones have their own
    schedules — the city slots close at 12:00, 18:00, 21:00, 22:30 and midnight,
    the outer ones at 17:00 and midnight — and wherever two of them close on the
    same minute, the orders waiting in both leave together on one courier order.
    Two vans setting off from the same kitchen at the same moment is two lots of
    the base fare for one journey's worth of work; the courier optimises the
    combined route and everything on it gets cheaper.

    `polygon_id` and `window_id` record which zone's slot opened the run. They
    describe where it came from, not what is on it.
    """
    result = await db.execute(
        select(DeliveryBatch)
        .where(
            DeliveryBatch.dispatch_at == match.dispatch_at,
            DeliveryBatch.status == BatchStatusEnum.PENDING.value,
        )
        # Oldest first, so every zone closing on this minute converges on the
        # same run instead of two of them each creating one.
        .order_by(DeliveryBatch.created_at)
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

    windows = await active_windows(db, delivery.polygon_id)
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
    windows = await active_windows(db, polygon_id)
    now = datetime.now(timezone.utc)

    # Selected by the *order's* zone rather than the batch's. A run is shared
    # across every zone closing on the same minute, so the batch it sits in may
    # well have been opened by a different zone's schedule — and editing this
    # zone's windows must not drag those other orders around with it.
    waiting = (
        (
            await db.execute(
                select(OrderDelivery)
                .join(DeliveryBatch, DeliveryBatch.id == OrderDelivery.batch_id)
                .where(
                    OrderDelivery.polygon_id == polygon_id,
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


# ── retry ─────────────────────────────────────────────────────────────────────


def _minutes_of(clock: str) -> int | None:
    """ "HH:MM" as a minute of the day, or None if it is not that."""
    try:
        hour, _, minute = clock.partition(":")
        total = int(hour) * 60 + int(minute)
    except ValueError:
        return None
    return total if 0 <= total <= 1440 else None


def kitchen_is_open(moment: datetime, opens_at: str, closes_at: str) -> bool:
    """
    Whether the branch is trading at this instant, on its own clock.

    Same half-open reading as a batch window, and the same tolerance for a day
    that runs past midnight: a kitchen open 09:00–02:00 is open at 01:00.
    Unparseable hours are treated as always open — a retry that might be too
    late beats no retry at all because a branch record had a typo in it.
    """
    opens, closes = _minutes_of(opens_at), _minutes_of(closes_at)
    if opens is None or closes is None:
        return True
    local = _local(moment)
    minute = local.hour * 60 + local.minute
    if closes <= opens:  # trades past midnight
        return minute >= opens or minute < closes
    return opens <= minute < closes


def _retry_at(
    batch: DeliveryBatch,
    now: datetime,
    *,
    opens_at: str = "00:00",
    closes_at: str = "23:59",
) -> datetime | None:
    """
    When this run should be tried again, or None if it should not be.

    Two things end the ladder. Running out of rungs, which is the ordinary case
    and means the failure has outlived the kind of problem that fixes itself.
    And landing after the kitchen has shut, which matters more than it sounds:
    the driver collects from a physical counter, and a booking made at 00:05 for
    a run that failed at 23:00 sends someone to a dark shop. That one waits for
    the morning and a human, which is what would have happened anyway.
    """
    if batch.attempt_count > len(RETRY_BACKOFF):
        return None
    when = now + RETRY_BACKOFF[batch.attempt_count - 1]
    if not kitchen_is_open(when, opens_at, closes_at):
        return None
    return when


def _fail(
    batch: DeliveryBatch,
    message: str,
    *,
    retry_at: datetime | None = None,
) -> None:
    """
    Record a refusal, and whether anything will come of it on its own.

    A run that has been booked at all keeps reading as dispatched, however badly
    the rest of it went. `courier_order_id` is only ever set by a booking that
    succeeded, so it is the honest test for "is a driver already carrying part
    of this" — and marking that run failed would send somebody looking for a van
    that is out on the road.
    """
    on_the_road = batch.courier_order_id is not None
    batch.status = (
        BatchStatusEnum.DISPATCHED.value
        if on_the_road
        else BatchStatusEnum.FAILED.value
    )
    batch.last_error = message
    batch.next_attempt_at = retry_at
    if retry_at is not None:
        logger.info(
            "Batch %s failed (attempt %s); retrying at %s",
            batch.id,
            batch.attempt_count,
            retry_at.isoformat(),
        )
    else:
        logger.warning(
            "Batch %s failed after %s attempt(s) and needs a human: %s",
            batch.id,
            batch.attempt_count,
            message,
        )


# ── dispatch ──────────────────────────────────────────────────────────────────


async def dispatch_due_batches(db: AsyncSession, *, limit: int = 20) -> list[uuid.UUID]:
    """
    Send every run whose time has come — its window closing, or a retry falling due.

    The two are one query on purpose. A retry is not a different kind of work,
    it is the same run at a later minute, so `next_attempt_at` is read on its own
    without asking what status the row is in. That covers the three ways a run
    can be left with work outstanding — refused outright, half-booked, or stuck
    mid-flight because the process died between the claim and the courier's
    reply — without a special case for each.

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
                    or_(
                        and_(
                            DeliveryBatch.status == BatchStatusEnum.PENDING.value,
                            DeliveryBatch.dispatch_at <= now,
                        ),
                        and_(
                            DeliveryBatch.next_attempt_at.isnot(None),
                            DeliveryBatch.next_attempt_at <= now,
                        ),
                    )
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
            # A bug or a database failure, not a courier refusal. The ladder
            # still applies — this is the one path that cannot check the
            # kitchen's hours, because whatever just went wrong may have taken
            # the session with it and a query here would abort the whole sweep.
            # The ladder alone bounds it to about an hour.
            logger.exception("Batch %s blew up while dispatching", batch.id)
            _fail(
                batch,
                "Dispatch failed unexpectedly",
                retry_at=_retry_at(batch, now),
            )
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

    Every exit either books the run or decides, then and there, whether another
    attempt could change the answer. Only here is that knowable: the sweep sees
    a failed row, but this is the code that knows whether the courier said "not
    now" or "not ever".
    """
    now = datetime.now(timezone.utc)
    batch.attempt_count += 1
    # Cleared on the way in rather than on the way out, so a process that dies
    # mid-booking leaves a row that is claimed and quiet instead of one every
    # worker re-picks on every sweep. Each exit below sets it again if it should.
    batch.next_attempt_at = None

    #: Drops a previous attempt already put on the road. `_ready_deliveries`
    #: never hands those back, so everything below has to add to them rather
    #: than speak as if this attempt were the only one.
    already_booked = batch.stop_count if batch.courier_order_id else 0

    deliveries = await _ready_deliveries(db, batch.id)
    if not deliveries:
        if already_booked:
            # A retry whose leftovers were cancelled while it waited. The run
            # itself went out and a driver is carrying the rest of it; calling
            # that cancelled would be a lie about a van already on the road.
            batch.status = BatchStatusEnum.DISPATCHED.value
            logger.info("Batch %s has nothing left to book", batch.id)
            return batch
        batch.status = BatchStatusEnum.CANCELLED.value
        batch.stop_count = 0
        logger.info("Batch %s had nothing ready to send", batch.id)
        return batch

    if not lalamove_service.is_enabled():
        # Configuration, not weather. Another attempt in five minutes reads the
        # same settings and fails the same way.
        _fail(batch, "Courier is not configured; dispatch these orders by hand")
        return batch

    # Every order on a run shares a zone, and a zone names one kitchen — so the
    # run has one collection point by construction. Read off the polygon rather
    # than the batch: the batch is a schedule, the polygon is the geography.
    polygon = await db.get(DeliveryPolygon, batch.polygon_id)
    pickup = await lalamove_service.resolve_pickup(
        db, polygon.branch_id if polygon else None
    )
    if pickup is None:
        _fail(batch, "No pickup branch is configured")
        return batch

    def retry_at() -> datetime | None:
        return _retry_at(
            batch, now, opens_at=pickup.opens_at, closes_at=pickup.closes_at
        )

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
        # The addresses are what they are. Nothing about waiting makes them
        # usable, so this one goes to a human to fix or to cancel.
        _fail(batch, "No order in this run had a usable address")
        if not already_booked:
            batch.stop_count = 0
        return batch

    chunks = [
        drops[i : i + MAX_DROPS_PER_ORDER]
        for i in range(0, len(drops), MAX_DROPS_PER_ORDER)
    ]
    booked = 0
    errors: list[str] = []
    #: Only a refusal that could go the other way next time. An address the
    #: courier does not serve is not one of those.
    worth_retrying = False
    for index, chunk in enumerate(chunks):
        try:
            await _book_chunk(db, batch, pickup, chunk, part=index, parts=len(chunks))
            booked += len(chunk)
        except LalamoveError as exc:
            errors.append(str(exc))
            worth_retrying = worth_retrying or not exc.is_out_of_service_area
            for delivery, _ in chunk:
                delivery.last_error = str(exc)
            logger.warning("Batch %s part %s failed: %s", batch.id, index + 1, exc)

    batch.stop_count = already_booked + booked
    # A refusal with no message at all is the shape of a transient fault, so it
    # earns the benefit of the doubt; a refusal that named an unserviceable
    # address does not.
    retry = retry_at() if worth_retrying or not errors else None

    if booked == 0:
        # `_fail` keeps a partly-booked run reading as dispatched: the leftovers
        # of one failing again does not recall the driver who has the rest.
        _fail(batch, errors[0] if errors else "Courier refused the run", retry_at=retry)
        return batch

    batch.status = BatchStatusEnum.DISPATCHED.value
    # Kept from the first booking on a retry: the run left when it left.
    batch.dispatched_at = batch.dispatched_at or datetime.now(timezone.utc)
    batch.last_error = "; ".join(errors) or None
    # Some of it went and some of it did not. The run reads as dispatched
    # because a driver really is coming, but the orders in the failed part are
    # still sitting in the kitchen unbooked — so it comes back on the ladder,
    # and `_ready_deliveries` will hand it only the ones without a courier
    # order. Nothing already on the road can be booked twice.
    if booked < len(drops):
        batch.next_attempt_at = retry
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

    # The first booking owns the batch-level fields; every later one adds its
    # cost, so the total is what the whole run cost rather than what one third
    # of it did. Keyed on "has this batch been booked at all" rather than on the
    # part number, because a retry after a partial failure starts counting parts
    # again from zero and would otherwise overwrite the first courier order's id
    # and price with the second's.
    if batch.courier_order_id is None:
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
