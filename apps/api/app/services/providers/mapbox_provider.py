"""
Mapbox Directions — how long the drive actually takes, given traffic now.

The one question the counter asks that neither courier answers. Lalamove and
noon Send both report *where* a driver is and neither reports how far off they
are, and the straight line between two pins does not know about the creek, the
one-ways round Al Majaz, or which bridge is between the rider and the kitchen. A
driver two kilometres away as the crow flies can be six kilometres of road.

**`driving-traffic`, not `driving`.** The profile is the whole point: the free
profile returns a free-flow duration, which is a different number from the one a
person waiting at a counter cares about at seven in the evening. `exclude=toll`
because Salik gates sit between several of our zones and a rider on a bike does
not pay them — matching the call shape already proven in production in
`sc-food-api`.

**Never raises.** Every caller is a sweep that must not die on one row or a
webhook that must answer 200, and the thing on the other end is somebody else's
HTTP service. A failure returns `None` and the caller falls back to the
straight-line estimate, which is what shipped before this existed. A counter
that sees "~4.2 km away" instead of "6 min · 4.2 km" has lost precision; a
counter that sees a 500 has lost the order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["Route", "is_configured", "route"]

_BASE = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"


@dataclass(frozen=True)
class Route:
    """One driven leg, as Mapbox measured it against current traffic."""

    #: Road kilometres, not straight line.
    distance_km: float
    #: Minutes of driving, with traffic as it is right now.
    minutes: float


def is_configured() -> bool:
    return bool((settings.MAPBOX_ACCESS_TOKEN or "").strip())


async def route(
    *,
    from_latitude: float,
    from_longitude: float,
    to_latitude: float,
    to_longitude: float,
) -> Route | None:
    """
    The drive between two points, or `None` if we could not get one.

    Coordinates go in the URL as `lng,lat` — Mapbox's order, which is the
    reverse of how every other part of this codebase writes a pin, and the
    mistake it produces is a route across the Arabian Sea rather than an error.
    Named arguments throughout so a caller cannot silently transpose them.
    """
    if not is_configured():
        return None

    path = f"{_BASE}/{from_longitude},{from_latitude};{to_longitude},{to_latitude}"
    try:
        async with httpx.AsyncClient(timeout=settings.MAPBOX_TIMEOUT_S) as client:
            response = await client.get(
                path,
                params={
                    "access_token": settings.MAPBOX_ACCESS_TOKEN.strip(),
                    # Salik sits between several of our zones and a rider on a
                    # bike does not pay it.
                    "exclude": "toll",
                    # We want the numbers, not a line to draw. Declining the
                    # geometry keeps the response small.
                    "overview": "false",
                    "alternatives": "false",
                },
            )
    except Exception:  # noqa: BLE001 — network, and never worth failing on
        logger.warning("Mapbox Directions was unreachable")
        return None

    if response.status_code != 200:
        # Logged at warning with the body truncated: a 401 here means the token
        # is wrong or URL-restricted, and that is a configuration mistake
        # somebody has to be able to find. It must still not raise.
        logger.warning(
            "Mapbox Directions answered %s: %s",
            response.status_code,
            response.text[:200],
        )
        return None

    try:
        routes = response.json().get("routes") or []
        if not routes:
            # A legitimate answer: no drivable route between the two points.
            # Happens on a pin dropped in the sea, which customers do.
            return None
        leg = routes[0]
        return Route(
            distance_km=round(float(leg["distance"]) / 1000, 1),
            minutes=round(float(leg["duration"]) / 60, 1),
        )
    except Exception:  # noqa: BLE001 — their shape, not ours
        logger.warning("Mapbox Directions returned something unreadable")
        return None
