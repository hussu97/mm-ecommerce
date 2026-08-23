"""
Distance between two points on the shop's own map.

One implementation, because two would eventually disagree about the same
kilometre. `noon_send_service` prices zones off this and `driver_proximity`
tells the counter how far away a rider is off this; a pricing band and a screen
that quote different numbers for the same pair of coordinates is a support call
nobody can resolve.

Deliberately *not* a great-circle formula. Everything asked of it lives inside
one emirate — fifteen kilometres at the outside — where a local equirectangular
projection is accurate to a fraction of a percent and is arithmetic rather than
four trigonometric calls per row.
"""

from __future__ import annotations

import math

__all__ = ["KM_PER_DEG_LAT", "straight_line_km"]

#: Kilometres per degree of latitude. Good to a fraction of a percent over the
#: fifteen kilometres this is ever asked about.
KM_PER_DEG_LAT = 111.32


def straight_line_km(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """As the crow flies, in kilometres. Never negative, never a road distance."""
    mean_lat = math.radians((lat_a + lat_b) / 2)
    dy = (lat_b - lat_a) * KM_PER_DEG_LAT
    dx = (lng_b - lng_a) * KM_PER_DEG_LAT * math.cos(mean_lat)
    return math.hypot(dx, dy)
