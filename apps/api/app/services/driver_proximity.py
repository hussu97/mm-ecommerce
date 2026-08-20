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

**Two answers, and it says which it gave.** Where Mapbox has routed the leg
recently, this returns driven road kilometres and minutes against live traffic —
the straight line does not know about the creek, the one-ways round Al Majaz, or
which bridge is between the rider and the shop, and a driver two kilometres away
as the crow flies can be six of road. Where it has not, this falls back to
straight line times the fitted detour factor and returns **no minutes at all**,
because a duration derived by dividing that estimate by an assumed speed would
be a guess dressed as a measurement.

Nobody bills from either. Both answer "ten minutes or two", which is all this is
ever asked.
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

    #: Kilometres from the driver to the branch — driven road distance when
    #: Mapbox answered, straight line times the detour factor otherwise.
    distance_km: float
    #: The moment the position behind it was reported.
    at: datetime
    #: Minutes of driving against traffic as it was at `at`, or `None` when the
    #: number came from the fallback.
    #:
    #: Null rather than derived from the distance. A duration invented by
    #: dividing kilometres by an assumed speed is a guess wearing the clothes of
    #: a measurement, and a counter cannot tell the two apart — 4 km through Al
    #: Majaz at eight in the evening is not 4 km at three in the afternoon.
    #: Nothing is better than a confident wrong number here.
    minutes: float | None = None

    @property
    def is_routed(self) -> bool:
        """Measured on real roads rather than estimated from a straight line."""
        return self.minutes is not None


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

    # ── the routed answer, when there is a fresh one ──────────────────────────
    #
    # Aged against the same window as the position, on its own stamp. The two go
    # stale for different reasons — a rider moves, and separately Mapbox stops
    # answering or the token is wrong — so a fresh pin with an old route is a
    # real state and it must degrade to the estimate rather than quote a
    # duration for a road the driver left ten minutes ago.
    routed_at = delivery.driver_route_at
    if routed_at is not None and routed_at.tzinfo is None:
        routed_at = routed_at.replace(tzinfo=timezone.utc)
    if (
        routed_at is not None
        and delivery.driver_route_km is not None
        and delivery.driver_route_minutes is not None
        and (now or datetime.now(timezone.utc)) - routed_at <= MAX_AGE
    ):
        return Proximity(
            distance_km=float(delivery.driver_route_km),
            minutes=float(delivery.driver_route_minutes),
            at=routed_at,
        )

    # ── the fallback ──────────────────────────────────────────────────────────
    #
    # What shipped before Mapbox existed, and still correct to within a fitted
    # detour factor. No token, an unreachable router, a pin with no drivable
    # route: all of them land here, and a counter reading "~4.2 km away" rather
    # than "6 min · 4.2 km away" has lost precision and nothing else.
    straight = geo.straight_line_km(
        float(latitude),
        float(longitude),
        float(branch.latitude),
        float(branch.longitude),
    )
    return Proximity(
        distance_km=round(straight * settings.NOON_SEND_DETOUR_FACTOR, 1), at=at
    )
