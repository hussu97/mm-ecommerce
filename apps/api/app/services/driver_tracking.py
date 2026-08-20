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

**Bounded on purpose.** Only bookings with a driver who has not yet collected —
after pickup the distance-from-the-kitchen stops meaning anything, and paying
for an API call to compute it would be paying to mislead. Ordered by the stalest
position first and capped per tick, so a shop with an unusual number of live
orders spends a predictable number of calls rather than an unbounded one. A row
that errors is logged and skipped: this runs inside the batch sweep, and cakes
waiting for a van matter more than a kilometre on a screen.
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
from app.services import driver_assignment
from app.services.driver_assignment import Change, Driver

logger = logging.getLogger(__name__)

__all__ = ["STALE_AFTER", "refresh_live_drivers"]

#: How old a position has to be before it is worth an API call to replace.
#:
#: Comfortably inside `driver_proximity.MAX_AGE`, so a booking refreshed on one
#: tick is never stale by the next — the counter sees a number continuously
#: rather than one that blinks out between sweeps.
STALE_AFTER = timedelta(seconds=75)

#: The statuses in which a Lalamove driver is matched and still coming to us.
#: `ASSIGNING_DRIVER` is excluded because there is nobody to ask about yet.
_LIVE_LALAMOVE = (CourierStatusEnum.ON_GOING.value,)


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
    from app.services import lalamove_service

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
                    OrderDelivery.driver_id.is_not(None),
                    OrderDelivery.courier_status.in_(_LIVE_LALAMOVE),
                    or_(
                        OrderDelivery.driver_location_at.is_(None),
                        OrderDelivery.driver_location_at < cutoff,
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
    One booking: has the driver changed, and where are they now.

    The booking is read before the driver, and that order matters. Asking for a
    driver id we already hold would answer perfectly well about a person who
    handed the job on an hour ago — the courier is happy to describe them — so
    the swap has to be settled first and the position asked of whoever the
    answer names.
    """
    from app.services import lalamove_service
    from app.services.providers.lalamove_provider import provider

    booking = await provider.get_order(str(delivery.courier_order_id))
    data = booking.get("data") if isinstance(booking.get("data"), dict) else booking
    data = data or {}

    status = data.get("status")
    if is_terminal(delivery.provider, status) or is_collected(
        delivery.provider, status
    ):
        # It ended, or the parcel is already on the bike, between the query and
        # the call. Nothing to track; the status webhook owns the transition.
        return False

    driver_id = data.get("driverId")
    change = await driver_assignment.record(
        db, delivery, Driver.from_lalamove(None, driver_id=driver_id), at=at
    )

    if change is Change.UNCHANGED and not delivery.driver_id:
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
