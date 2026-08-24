"""
Keeping the driver on a live booking current, when the courier will not tell us.

**The two integrations report a rider completely differently, and only one of
them reports at all.** noon Send pushes a position every 15-30 seconds and a
`da_details` block with it, so their side needs nothing here. Lalamove pushes a
position exactly once — inside the driver detail we fetch when the id first
appears — and then goes quiet until the status changes. Left alone, "the driver
is 400 m away" on a counter screen would mean *was*, at the moment they were
assigned, twenty minutes ago.

**And a swap is invisible without this.** Lalamove's documented
`DRIVER_ASSIGNED` event has never once arrived in production; every driver id we
have has come in on a status change. A reassignment does not change the status —
the booking stays `ON_GOING` — so there is no push at all, and the shop would
keep the first driver's name and number until pickup. Reading the booking back
is the only thing that sees it.

So one sweep, on the minute, doing both: read the booking, notice if the driver
id moved, and refresh the position of whoever is on it now.

**And, having read it, notice when the booking has ended without us.** Owning
terminal transitions is the webhook's job and still is — but a push that never
arrives owns nothing. A lost `COMPLETED` leaves an order at `packed` after the
cake is in someone's hands: wrong in the shop's reports, wrong in the
customer's timeline, and invisible until a person goes looking, because there
is no retry from Lalamove to wait for. The poll already has the authoritative
answer in hand; `_reconcile_ending` feeds it through `apply_webhook` so the
ending is applied by the same code that would have applied the push.

**Bounded on purpose.** Only bookings that have not yet been collected — after
pickup the distance-from-the-kitchen stops meaning anything, and paying for an
API call to compute it would be paying to mislead — and only for as long as one
could still be in progress. Ordered by the stalest position first and capped per
tick, so a shop with an unusual number of live orders spends a predictable
number of calls rather than an unbounded one. A row that errors is logged and
skipped: this runs inside the batch sweep, and cakes waiting for a van matter
more than a kilometre on a screen.

**Not bounded on having a driver, which it used to be.** The query asked for
`driver_id IS NOT NULL`, so the one booking whose driver we had failed to
record was precisely the one this could never revisit — a backstop keyed off
the value the failure destroys. MM-20260821-001 spent its delivery that way:
a webhook naming its rider rolled back at commit, and both the driver and the
`ON_GOING` that came with it were lost, leaving a row that no sweep would look
at again. `_refresh_one` never needed the id — it reads the booking first and
learns the driver from that — so the filter bought nothing and cost the only
case it mattered in.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_delivery import (
    CourierStatusEnum,
    OrderDelivery,
    is_collected,
    is_terminal,
)
from app.services.delivery import driver_assignment
from app.services.delivery.driver_assignment import Change, Driver

logger = logging.getLogger(__name__)

__all__ = ["CHASE_FOR", "STALE_AFTER", "refresh_live_drivers"]

#: How old a position has to be before it is worth an API call to replace.
#:
#: Comfortably inside `driver_proximity.MAX_AGE`, so a booking refreshed on one
#: tick is never stale by the next — the counter sees a number continuously
#: rather than one that blinks out between sweeps.
STALE_AFTER = timedelta(seconds=75)

#: The statuses in which a booking is still coming to us.
#:
#: `ASSIGNING_DRIVER` used to be excluded, on the reasoning that there is nobody
#: to ask about yet. That is true of the status and not of the row: our copy of
#: it comes from the same webhooks that can be lost, so a booking sitting here
#: is either genuinely waiting for a rider or one whose assignment we dropped,
#: and those two are indistinguishable without asking. Reading the booking is
#: what tells them apart, and it is one call a minute against an order that is
#: already paid for.
_LIVE_LALAMOVE = (
    CourierStatusEnum.ON_GOING.value,
    CourierStatusEnum.ASSIGNING_DRIVER.value,
)

#: How long after booking the sweep keeps asking about a delivery.
#:
#: A backstop rather than the brake. A row normally leaves `_LIVE_LALAMOVE` the
#: moment the sweep reads an ending off the booking and applies it, so nothing
#: healthy runs anywhere near this. What it bounds is the pathological case —
#: a booking Lalamove never resolves, or one whose ending we cannot apply — so
#: that "poll it again next minute" cannot become forever. Long enough that no
#: real delivery reaches it: a cake still uncollected six hours after its van
#: was booked is a person's problem, not a sweep's.
CHASE_FOR = timedelta(hours=6)


async def refresh_live_drivers(
    db: AsyncSession,
    *,
    limit: int = 25,
    now: datetime | None = None,
) -> int:
    """
    Re-read every live Lalamove booking. Returns how many were refreshed.

    Called from `batch_scheduler.sweep_once`, which already holds the advisory
    lock that makes one worker in the deployment responsible for periodic work.
    Standing up a second loop with a second lock for this would be a second
    thing to notice had stopped happening.
    """
    from app.services.couriers import lalamove_service

    if not lalamove_service.is_enabled():
        return 0

    moment = now or datetime.now(timezone.utc)
    cutoff = moment - STALE_AFTER

    rows = (
        (
            await db.execute(
                select(OrderDelivery)
                .where(
                    OrderDelivery.provider == lalamove_service.PROVIDER,
                    OrderDelivery.courier_order_id.is_not(None),
                    OrderDelivery.courier_status.in_(_LIVE_LALAMOVE),
                    or_(
                        OrderDelivery.driver_location_at.is_(None),
                        OrderDelivery.driver_location_at < cutoff,
                    ),
                    # Only while it could still be a delivery in progress.
                    or_(
                        OrderDelivery.booked_at.is_(None),
                        OrderDelivery.booked_at > moment - CHASE_FOR,
                    ),
                )
                .order_by(OrderDelivery.driver_location_at.asc().nullsfirst())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    refreshed = 0
    for delivery in rows:
        try:
            if await _refresh_one(db, delivery, at=moment):
                refreshed += 1
        except Exception:  # noqa: BLE001 — one bad booking must not stop the rest
            logger.exception(
                "Could not refresh the driver on booking %s",
                delivery.courier_order_id,
            )
    return refreshed


async def _refresh_one(
    db: AsyncSession, delivery: OrderDelivery, *, at: datetime
) -> bool:
    """
    One booking: has it ended, has the driver changed, and where are they now.

    The booking is read before the driver, and that order matters. Asking for a
    driver id we already hold would answer perfectly well about a person who
    handed the job on an hour ago — the courier is happy to describe them — so
    the swap has to be settled first and the position asked of whoever the
    answer names.

    Ending is settled before either, for the same reason: there is no driver to
    chase and no position worth paying for on a booking that is already over,
    and if nobody told us it was over then that is the most important thing this
    call learned.
    """
    from app.services.couriers import lalamove_service
    from app.services.providers.lalamove_provider import provider

    booking = await provider.get_order(str(delivery.courier_order_id))
    data = booking.get("data") if isinstance(booking.get("data"), dict) else booking
    data = data or {}

    status = data.get("status")
    if is_terminal(delivery.provider, status) or is_collected(
        delivery.provider, status
    ):
        # It ended, or the parcel is already on the bike. Nothing left to track
        # either way — but whether anybody *told* us is a separate question from
        # whether it happened, and this is the only place both answers are in
        # hand at once.
        if status == delivery.courier_status:
            # The push arrived and did its work; we are simply the tick that
            # noticed. The ordinary case, and the cheap one.
            return False
        return await _reconcile_ending(db, delivery, data, status, at=at)

    # Our copy of a *live* status, corrected against the booking itself. Safe
    # here and nowhere else: the two statuses left after the guard above are
    # `ASSIGNING_DRIVER` and `ON_GOING`, and neither advances the order, so this
    # writes a column rather than triggering a transition. It matters because a
    # lost webhook takes the status with it — MM-20260821-001 read
    # `ASSIGNING_DRIVER` for its whole delivery while Lalamove had said
    # `ON_GOING` — and a status nothing ever corrects is one every later sweep
    # filters on and gets wrong.
    if status and status != delivery.courier_status:
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_status = status
        # Stamped with it, as every other writer of this column does. A status
        # that moves without its timestamp leaves the column quietly claiming
        # the last webhook's moment for a change this sweep made.
        delivery.status_updated_at = at

    driver_id = data.get("driverId")
    change = await driver_assignment.record(
        db, delivery, Driver.from_lalamove(None, driver_id=driver_id), at=at
    )

    if change is Change.UNCHANGED and not delivery.driver_id:
        # Nobody is on it yet — the ordinary state of a booking still being
        # matched. There is no driver to name and no position to ask for.
        return False

    # Names and numbers live on the driver endpoint, not the order — so a swap
    # needs a second call before the shop can be told anything useful. An
    # unchanged driver needs it too, because that is also where the position is.
    await lalamove_service.fill_driver_details(db, delivery, at=at)

    if change.is_new_driver:
        await lalamove_service.announce_driver(db, delivery)
        logger.info(
            "Driver %s on booking %s (%s)",
            change.value,
            delivery.courier_order_id,
            delivery.driver_name or delivery.driver_id or "?",
        )
    return True


async def _reconcile_ending(
    db: AsyncSession,
    delivery: OrderDelivery,
    order: dict,
    status: str | None,
    *,
    at: datetime,
) -> bool:
    """
    Apply an ending the webhook for it never delivered.

    The sweep used to return here and leave every terminal transition to the
    push, which is right about *ownership* and wrong about what to do when the
    push does not come. A `COMPLETED` that goes missing leaves an order sitting
    at `packed` — delivered in the world, undelivered in the shop's reports, in
    the customer's timeline and in the till — until a person happens to look.
    There is no retry from Lalamove's side to wait for: a webhook we answered
    `200` to, or one that never left them, is equally gone.

    **Through `apply_webhook`, not around it.** Ending a booking is not one
    column. It is the order's own transition and the email that rides on it, a
    refund path for the failures, the price breakdown, the proof of delivery,
    the cancellation reason an admin reads. Writing a second copy of that here
    would be a second thing to keep in step with `VALID_TRANSITIONS`, and the
    first copy is already correct. So this fabricates the push that should have
    arrived and feeds it through the same door.

    The payload is stamped *now* rather than with the courier's `updatedAt`,
    which is Gulf local time wearing a `Z` and is the reason `webhook_time`
    exists. Now is both honest — this is when we learned it — and the only
    stamp that reliably clears the out-of-order guard, since the genuine event
    may be older than the last push we did receive.
    """
    from app.services.couriers import lalamove_service

    logger.warning(
        "Booking %s is %s and nothing told us; reconciling from the poll (we had %s)",
        delivery.courier_order_id,
        status,
        delivery.courier_status,
    )
    await lalamove_service.apply_webhook(db, _as_payload(order, at=at), delivery)
    return True


def _as_payload(order: dict, *, at: datetime) -> dict:
    """
    A polled booking in the shape `apply_webhook` reads.

    `eventType` is deliberately not one of Lalamove's own. Their names carry
    promises about the body — `DRIVER_ASSIGNED` means a whole person is
    described, `POD_STATUS_CHANGED` means a proof is attached — and the order
    endpoint makes neither. Borrowing a name to get past a branch would put a
    lie in `last_payload`, which is the first thing anybody reads when asking
    what the courier actually said. This says where it came from instead, and
    `apply_webhook` falls through both branches, which is the correct handling
    of a body that has neither.
    """
    return {
        "eventType": "POLLED_ORDER_STATUS",
        "timestamp": at.timestamp(),
        "data": {"order": order},
    }
