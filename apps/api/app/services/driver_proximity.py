"""
How far the driver still is from the kitchen.

The counter's question, and it is a narrow one: *is it worth boxing this now, or
is the rider ten minutes out?* Somebody stands over the Packed tab holding a
cake waiting for a person to walk in, and until this existed the only honest
thing the screen could say was the driver's name.

**Computed here, quoted everywhere.** The register and the admin both show it,
and CLAUDE.md's money rule generalises: a client-side formula mirroring a server
one is a bug, and two screens deriving a kilometre from raw coordinates would
eventually disagree about the same driver. They render what this quotes.

**Three ways it declines to answer, all deliberate.**

* *No position, or one of unknown age.* Every distance carries the moment it was
  true. A pin with no `driver_location_at` is one written by whichever webhook
  happened to carry coordinates, at a time nobody recorded — see migration 112,
  which leaves those null rather than dating them.
* *A position that has gone stale.* A rider moves; a number that stopped moving
  fifteen minutes ago is not "where they are", it is where they were. Past
  `MAX_AGE` this returns nothing rather than a stale figure, because a counter
  cannot tell the two apart and would act on both. noon Send pushes a position
  every 15-30 seconds and `driver_tracking` refreshes Lalamove's each minute, so
  a live booking is comfortably inside the window and a silent integration
  correctly falls quiet.
* *The parcel has already been collected.* From then on the driver is supposed
  to be getting further away, and a growing distance-from-the-kitchen on a
  counter screen is worse than no distance at all.

**And it is an estimate, labelled as one.** Straight line times the same detour
factor the zone pricing is fitted against. Nobody bills from it; it answers "ten
minutes or two" and that is all it is asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.models.branch import Branch
from app.models.order_delivery import OrderDelivery
from app.services import geo

__all__ = ["MAX_AGE", "Proximity", "to_pickup"]

#: How old a position may be and still be worth quoting.
#:
#: Six minutes, against a 15-30 second push from noon Send and a one-minute
#: sweep for Lalamove — so an integration would have to miss five consecutive
#: updates before the counter stops being told anything. Tight enough that a
#: dead feed goes quiet rather than lying, loose enough that one dropped push
#: does not blank the screen.
MAX_AGE = timedelta(minutes=6)


@dataclass(frozen=True)
class Proximity:
    """How far away the driver is, and when that was true."""

    #: Estimated road kilometres from the driver to the branch.
    distance_km: float
    #: The moment the position behind it was reported.
    at: datetime


def to_pickup(
    delivery: OrderDelivery | None,
    branch: Branch | None,
    *,
    now: datetime | None = None,
) -> Proximity | None:
    """
    The driver's distance from *branch*, or nothing if we cannot say honestly.

    Takes the objects rather than six floats so that every caller is asking the
    same question of the same two rows. Returns `None` far more often than not —
    most orders have no courier, no driver yet, or a driver who has already been.
    """
    if delivery is None or branch is None:
        return None
    if not delivery.is_driver_on_the_way_here:
        return None

    at = delivery.driver_location_at
    latitude = delivery.driver_latitude
    longitude = delivery.driver_longitude
    if at is None or latitude is None or longitude is None:
        return None
    if branch.latitude is None or branch.longitude is None:
        return None

    # A stamp read back off a naive column would compare unequal to an aware
    # clock and raise. Treated as UTC, which is what everything writes.
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if (now or datetime.now(timezone.utc)) - at > MAX_AGE:
        return None

    straight = geo.straight_line_km(
        float(latitude),
        float(longitude),
        float(branch.latitude),
        float(branch.longitude),
    )
    return Proximity(
        distance_km=round(straight * settings.NOON_SEND_DETOUR_FACTOR, 1), at=at
    )
