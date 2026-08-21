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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.delivery_batch import (
    DELIVERY_TIMEZONE,
    MAX_DROPS_PER_ORDER,
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchGroup,
    DeliveryBatchWindow,
)
from app.core import trading_hours
from app.core.exceptions import BadRequestError
from app.models.courier import Courier
from app.models.delivery_polygon import DeliveryPolygon, FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import (
    CourierStatusEnum,
    OrderDelivery,
    is_failed,
)
from app.services import (
    courier_reference,
    courier_service,
    driver_assignment,
    lalamove_service,
)
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
    "reserve",
    "cancel_assignment",
    "shared_run_booking",
    "dispatch_batch",
    "dispatch_due_batches",
    "find_window",
    "assert_group_fits_polygon",
    "group_for_polygon",
    "kitchen_is_open",
    "next_dispatch_at",
    "overlapping",
    "reschedule_group",
    "retry_failed_dispatches",
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
    db: AsyncSession, group_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    """This group's live schedule, earliest first."""
    result = await db.execute(
        select(DeliveryBatchWindow)
        .where(
            DeliveryBatchWindow.group_id == group_id,
            DeliveryBatchWindow.is_active.is_(True),
        )
        .order_by(DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute)
    )
    return list(result.scalars().all())


async def _open_batch(
    db: AsyncSession,
    group_id: uuid.UUID,
    match: WindowMatch,
) -> DeliveryBatch:
    """
    The run this order joins, created if it is the first one leaving then.

    Keyed on `(group, dispatch_at)`. It used to be keyed on `dispatch_at`
    **alone**, which meant any two zones whose slots happened to close on the
    same minute were merged onto one courier booking — a decision nobody made,
    nothing displayed, and that silently changed whenever somebody edited an
    unrelated zone's schedule.

    Sharing a run is still the point: one route carrying five drops costs about
    a third per delivery of five separate ones. But which zones share it is now
    the group, which somebody declared, so the Dubai bands ride together because
    they were put together — and the northern zones closing at the same instant
    get their own van rather than being folded into Dubai's.
    """
    result = await db.execute(
        select(DeliveryBatch)
        .where(
            DeliveryBatch.group_id == group_id,
            DeliveryBatch.dispatch_at == match.dispatch_at,
            DeliveryBatch.status == BatchStatusEnum.PENDING.value,
        )
        # Oldest first, so every zone in this group converges on the same run
        # instead of each creating one.
        .order_by(DeliveryBatch.created_at)
    )
    batch = result.scalars().first()
    if batch is not None:
        return batch

    batch = DeliveryBatch(
        group_id=group_id,
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
    The single entry point when an order becomes something to call a driver for.

    Either it joins a run that has not left yet, or it goes on its own right
    now. Third-party zones fall straight through and keep the manual flow they
    have always had.

    **Called on arrival, not on acceptance and not on packing.** Reaching the
    register is the moment the shop is committed to the order, and on a batched
    zone it is also the moment the run leaves — the two are one act by design,
    which is what puts a courier reference on the ticket that prints. `packed`
    still calls this as a backstop, for the order an admin marks finished before
    any sweep has reached it. Both triggers are safe because this function is
    idempotent: an order already on a run, or already booked, returns untouched
    at the guards below.

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
    if delivery.batch_id:
        # On a run, and the run's machinery owns it from here — whether it is
        # still collecting, out with a driver, or failed and climbing its ladder
        # in `dispatch_due_batches`.
        #
        # Deliberately *any* batch and not only an open one. This used to ask
        # whether the batch was still collecting and fall through to a fresh
        # reservation when it was not, which meant an order arriving at the
        # instant its window closed matched the window that had just opened and
        # sat down to wait another three hours for a van. The order is on a run;
        # opening it a second one is never the answer.
        return delivery

    now = moment or datetime.now(timezone.utc)
    delivery.dispatchable_at = now

    batch = await reserve(db, order, moment=now, delivery=delivery)
    if batch is not None:
        return delivery
    return await courier_service.dispatch(db, order)


async def reserve(
    db: AsyncSession,
    order: Order,
    *,
    moment: datetime | None = None,
    delivery: OrderDelivery | None = None,
) -> DeliveryBatch | None:
    """
    Take this order's place on a shared run, and book nothing.

    Returns the run it joined, or None for an order that travels alone — which
    covers a third-party zone, a pickup, a noon Send zone, a Lalamove zone
    nobody put on a schedule, and a courier that is not configured at all. None
    is an answer rather than a gap, and the caller's job is to decide what
    "alone" means for it: `assign_or_dispatch` books a driver on the spot, and
    `arrival_service.schedule` reads it as "the shop can be told now".

    **Called at confirmation, well before anything is dispatched.** That
    ordering is the load-bearing part of the whole arrival design: a run is made
    of the orders assigned to it and its window closing is what tells the shop
    about them, so an order that waited to be told before joining would be
    waiting on a run it was never on. Nothing here contacts a courier.

    Idempotent by way of `_open_batch`, which converges every order in a group
    on one run per departure.
    """
    now = moment or datetime.now(timezone.utc)
    if delivery is None:
        delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        return None
    if not courier_service.books_itself(delivery.provider):
        # A third-party zone. Somebody else's van, on somebody else's schedule.
        return None

    # Who will *actually* carry it, which is not always what the zone says: a
    # Slider zone falls back to Lalamove for every customer outside the pilot,
    # and those orders must keep riding the run they have always ridden.
    # Asking `delivery.provider` here instead would take every Dubai and Ajman
    # order off its batch the day the map named Slider, and send each one alone
    # at roughly three times the cost with nothing on screen to say why.
    carrier, _ = courier_service.carrier_for(order, delivery)
    if carrier != FulfilmentProviderEnum.LALAMOVE.value:
        # noon Send books one order at a time against a different endpoint with
        # a cap of three drops, and Slider has no multi-stop product at all.
        # Neither is a run and neither can be shared.
        return None

    if not lalamove_service.is_enabled():
        # No courier configured, so there is no shared run to wait for. Saying
        # "alone" here records "dispatch this by hand" on the order immediately,
        # where the person packing it will see it — rather than parking it in a
        # batch that can only fail when its window closes an hour later.
        return None

    if delivery.polygon_id is None:
        # An order placed before zones carried an id, or against a map that has
        # since been deleted. It still has to go out; it just goes alone.
        return None

    group_id = await group_for_polygon(db, delivery.polygon_id)
    if group_id is None:
        # A zone in no group. That is not a gap — it is the declared answer for
        # every noon Send zone and every Lalamove zone nobody put on a schedule:
        # nothing to wait for, so it leaves now.
        logger.info(
            "Zone %s is in no batch group; order %s travels on its own",
            delivery.polygon_id,
            order.order_number,
        )
        return None

    windows = await active_windows(db, group_id)
    match = find_window(windows, now)
    if match is None:
        logger.info(
            "No batch window covers %s for order %s; it travels on its own",
            _local(now).strftime("%H:%M"),
            order.order_number,
        )
        return None

    batch = await _open_batch(db, group_id, match)
    delivery.batch_id = batch.id
    delivery.last_error = None
    # The short number the driver will quote, spent now rather than at the end
    # of the window. The ticket prints when the run leaves and the booking
    # happens in the same breath, so a reference assigned by the booking would
    # be racing the paper; assigned here it is hours old by then. It identifies
    # the order rather than the booking, and `assign` is idempotent, so the
    # dispatch finds this one already on the row and keeps it.
    await courier_reference.assign(db, delivery)
    batch.stop_count = await _count_deliveries(db, batch.id)
    logger.info(
        "Order %s joins %s, leaving %s",
        order.order_number,
        batch.window_label,
        batch.dispatch_at.isoformat(),
    )
    return batch


async def shared_run_booking(
    db: AsyncSession, delivery: OrderDelivery
) -> DeliveryBatch | None:
    """
    The run whose booking this delivery is riding, if the booking is not its own.

    A batched dispatch books **one** Lalamove order for up to fifteen stops and
    writes that single `orderId` onto every delivery in the chunk — see
    `_book_chunk`. So `delivery.courier_order_id` is ambiguous by design: on an
    order that went out alone it identifies that order's booking, and on a
    batched one it identifies a van carrying other people's cakes too.

    Nothing used to ask which. `cancel_delivery` takes that id straight to
    `DELETE /v3/orders/{id}`, so calling off one order on a booked run would
    have called off the run — four other customers' deliveries cancelled to
    stop one, with nothing anywhere saying that had happened.

    **A run of one is not shared.** A window that closed with a single order in
    it still produces a batch and still books through `_book_chunk`, so the
    delivery and the batch carry the same id — and cancelling it harms nobody,
    because there is nobody else on it. Counted rather than read off
    `batch.stop_count`: that column is what was *booked*, and it does not move
    when an order later leaves a dispatched run, so trusting it would keep
    protecting a van that is down to one stop.

    Returns None for the ordinary case: no run, no booking, a booking the
    delivery owns outright, or a run carrying only this order.
    """
    if not delivery.batch_id or not delivery.courier_order_id:
        return None
    batch = await db.get(DeliveryBatch, delivery.batch_id)
    if batch is None or batch.courier_order_id != delivery.courier_order_id:
        return None

    others = (
        await db.execute(
            select(func.count())
            .select_from(OrderDelivery)
            .where(
                OrderDelivery.courier_order_id == delivery.courier_order_id,
                OrderDelivery.id != delivery.id,
            )
        )
    ).scalar() or 0
    return batch if others else None


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
    # The session is `autoflush=False`, so the assignment the caller has just
    # made — this order joining the run, or being taken off it — is still only
    # in memory and a bare SELECT counts the run as it was a moment ago. That is
    # how the first live batch came to sit on the admin screen reading "0 drops"
    # while holding an order.
    await db.flush()
    result = await db.execute(
        select(OrderDelivery.id).where(OrderDelivery.batch_id == batch_id)
    )
    return len(result.scalars().all())


# ── rescheduling ──────────────────────────────────────────────────────────────


async def reschedule_group(db: AsyncSession, group_id: uuid.UUID) -> int:
    """
    Re-derive every waiting assignment in this group against the current windows.

    Called after the schedule is edited. An order whose window moved lands on
    the new one; an order whose window disappeared, or whose new window has
    already closed, goes out on its own instead of waiting for a slot that will
    not come round until tomorrow.

    Scoped to the group rather than one zone, which is now the same set the
    schedule actually governs. It used to be per-polygon and carried a careful
    note about not dragging other zones' orders around — a hazard that existed
    only because unrelated zones could end up sharing a run by accident. They
    cannot any more, so the scope and the schedule finally describe the same
    thing.

    Returns how many assignments changed.
    """
    windows = await active_windows(db, group_id)
    now = datetime.now(timezone.utc)

    waiting = (
        (
            await db.execute(
                select(OrderDelivery)
                .join(DeliveryBatch, DeliveryBatch.id == OrderDelivery.batch_id)
                .where(
                    DeliveryBatch.group_id == group_id,
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

        batch = await _open_batch(db, group_id, match)
        if batch.id == delivery.batch_id:
            continue
        await cancel_assignment(db, delivery)
        delivery.batch_id = batch.id
        # The run is what tells the shop about the order, so moving the run
        # moves that too. Without this an order dragged from a 21:00 slot to an
        # 18:00 one would leave with a driver three hours before the kitchen was
        # told to make it.
        if delivery.order is not None:
            delivery.order.arrives_at = batch.dispatch_at
        moved += 1

    await db.flush()
    for batch_id in {b for b in (d.batch_id for d in waiting) if b}:
        batch = await db.get(DeliveryBatch, batch_id)
        if batch is not None and batch.is_open:
            batch.stop_count = await _count_deliveries(db, batch_id)

    for delivery in strays:
        if delivery.order is None:
            continue
        # No slot left to wait for, so this one travels alone — and an order
        # travelling alone is due at the register now rather than at a departure
        # that no longer exists.
        delivery.order.arrives_at = now
        await courier_service.dispatch(db, delivery.order)

    if moved:
        logger.info("Rescheduled %s waiting orders in group %s", moved, group_id)
    return moved


# ── retry ─────────────────────────────────────────────────────────────────────


#: Whether the branch is trading, from `core.trading_hours`.
#:
#: It was defined here and the delivery promise needed the same question
#: answered. Two implementations of "is the kitchen open" is how a customer gets
#: told "tomorrow" for an order the dispatcher already knows cannot be started
#: until the day after. Re-exported under its old name so this module's callers
#: and tests do not move.
kitchen_is_open = trading_hours.is_open


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


async def retry_failed_dispatches(
    db: AsyncSession, *, limit: int = 20
) -> list[uuid.UUID]:
    """
    Ask again for every order whose retry has fallen due.

    The batch sweep above has always existed; this is its missing half. An order
    that failed on the *un-batched* path is in no batch, so nothing in
    `dispatch_due_batches` could ever see it — and the `packed` transition that
    first tried it had already happened. Until this function it waited for a
    human to notice a red box on an admin screen. MM-20260815-001 waited six
    hours, on an error a deploy had fixed after twenty-six minutes.

    **Re-entering at `assign_or_dispatch`, not `courier_service.dispatch`.** The
    order's first attempt may have taken the single-order path only because the
    courier was misconfigured at that moment — that guard sits above the batch
    lookup. Retrying at the top means the fix puts the order on a shared run,
    which is where its zone's schedule always said it belonged. Retrying at the
    bottom would condemn it to travel alone because of a credential that is no
    longer missing.

    `moment` is pinned to the order's original `dispatchable_at` so a retry
    matches the window the order actually entered the queue in, rather than
    whatever window happens to be open an hour later.
    """
    now = datetime.now(timezone.utc)
    due = (
        (
            await db.execute(
                select(OrderDelivery)
                .where(
                    OrderDelivery.next_attempt_at.isnot(None),
                    OrderDelivery.next_attempt_at <= now,
                    OrderDelivery.courier_order_id.is_(None),
                )
                .order_by(OrderDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    retried: list[uuid.UUID] = []
    for delivery in due:
        order = await db.get(Order, delivery.order_id)
        if order is None:  # pragma: no cover — FK is RESTRICT
            delivery.next_attempt_at = None
            continue
        # A settled order stops asking. The migration's backfill already bounds
        # this, but an order refunded *between* two rungs would otherwise have a
        # driver called for it, and a cake going out for a refunded order is
        # worse than a late one.
        if order.status not in _RETRYABLE_STATUSES:
            logger.info(
                "Order %s is %s; abandoning its retry",
                order.order_number,
                order.status.value,
            )
            delivery.next_attempt_at = None
            await db.commit()
            continue

        # Cleared before the attempt, not after. `assign_or_dispatch` returns
        # early on a delivery that joins a batch, and that path never reaches
        # `_record_outcome` — leaving the old due time in place would have the
        # sweep pick the same order up again on the next tick, forever.
        delivery.next_attempt_at = None
        try:
            await assign_or_dispatch(db, order, moment=delivery.dispatchable_at or now)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "Retry for order %s blew up; it will be left for a human",
                order.order_number,
            )
        await db.commit()
        retried.append(delivery.id)
    return retried


#: The statuses an order can be in and still want a driver. Anything else has
#: either already travelled or stopped being a delivery.
#:
#: `undelivered` is deliberately **not** here. It reads like a state wanting
#: another attempt — the cake exists and is paid for — and that is exactly the
#: reasoning that would have a sweep send a second van, unattended, for an
#: order somebody had already written off. A failed handover ends in a refund
#: conversation, and a person starts it.
_RETRYABLE_STATUSES = {
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
    OrderStatusEnum.PACKED,
}


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

    # A run collects from one kitchen. Every zone in a group is served by the
    # same branch — the group exists to share a van out of one door — so any of
    # its polygons answers, and the first is as good as any. Read off a polygon
    # rather than the batch because the batch is a schedule and the polygon is
    # the geography.
    polygon = (
        await db.execute(
            select(DeliveryPolygon)
            .where(DeliveryPolygon.batch_group_id == batch.group_id)
            .order_by(DeliveryPolygon.display_order)
            .limit(1)
        )
    ).scalar_one_or_none()
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
        # Every stop on a shared run gets its own short number. Fifteen cakes in
        # one van is exactly where a driver needs a per-drop reference they can
        # read out, rather than fifteen variations on `MM-20260805-0NN`.
        reference = await courier_reference.assign(db, delivery)
        drop, reason = lalamove_service.build_drop(delivery.order, reference)
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
        # Through the one writer, like every other dispatch. A fresh booking
        # carries nobody — Lalamove answers `ASSIGNING_DRIVER` and matches a
        # driver minutes later — and `clear` closes the stint left open by
        # whatever booking this run replaces, so a re-batched order does not
        # leave a rider from the abandoned one reading as current. Written
        # straight onto the column, that stint would have stayed active while
        # the delivery said nobody was assigned.
        await driver_assignment.clear(db, delivery, at=now)
        await driver_assignment.record(
            db,
            delivery,
            driver_assignment.Driver.from_lalamove(
                None, driver_id=booking.get("driverId")
            ),
            at=now,
        )
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
        # The run has a driver, so every box on it is finished as far as anyone
        # outside the shop is concerned — and nobody presses a button to say so
        # any more. Same stamp the single-order path applies on its own booking;
        # applied here too, because a batched order never passes through it.
        delivery.dispatch_attempts = 0
        delivery.next_attempt_at = None
        order = await db.get(Order, delivery.order_id)
        if order is not None:
            # Imported here, not at the top: `order_service` imports this module
            # to assign a batch, and closing that cycle at import time is a
            # crash on boot rather than a lint warning.
            from app.services import order_service

            await order_service.stamp_packed(
                db, order, note=f"batch {batch.window_label or batch.id} booked"
            )

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


async def group_for_polygon(
    db: AsyncSession, polygon_id: uuid.UUID
) -> uuid.UUID | None:
    """
    The active group this zone rides with, or None for one that leaves alone.

    None is an answer, not a gap: it is what every noon Send zone and every
    third-party zone says, and what a Lalamove zone nobody has put on a schedule
    says too. An inactive group reads as None for the same reason — switching a
    schedule off should send its zones out immediately, not park them against
    slots that will never fire.
    """
    result = await db.execute(
        select(DeliveryBatchGroup.id)
        .join(DeliveryPolygon, DeliveryPolygon.batch_group_id == DeliveryBatchGroup.id)
        .where(
            DeliveryPolygon.id == polygon_id,
            DeliveryBatchGroup.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def assert_group_fits_polygon(
    db: AsyncSession, polygon: DeliveryPolygon, group: DeliveryBatchGroup
) -> None:
    """
    Refuse a pairing that could never dispatch, with a message an admin can act on.

    Two ways it can be wrong, and both are silent otherwise — the orders simply
    accumulate in a batch that fails at its window, an hour after anyone could
    have done something about it.

    In the service rather than a database constraint deliberately: a zone's
    courier can change, and the useful outcome then is a readable refusal at the
    moment somebody tries it, not an integrity error surfacing as a 500.
    """
    courier = (
        await db.execute(select(Courier).where(Courier.code == group.courier_code))
    ).scalar_one_or_none()
    if courier is None or not courier.supports_batching:
        raise BadRequestError(
            f"{group.courier_code} orders cannot be batched, so "
            f"'{group.name}' cannot carry a schedule."
        )
    if polygon.fulfilment_provider != group.courier_code:
        raise BadRequestError(
            f"'{polygon.name}' is delivered by {polygon.fulfilment_provider}, "
            f"but '{group.name}' books {group.courier_code}. "
            "A run is one booking with one courier."
        )
