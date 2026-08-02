"""
Cut the emirate outlines into the zones the Lalamove fee strategy actually prices.

The strategy is not "per emirate". It is "per what a courier run costs from the
Sharjah kitchen", and an emirate is a poor proxy for that: Sharjah reaches Al
Dhaid and Khor Fakkan, Dubai reaches Hatta, Ajman owns two inland exclaves.
Those places cost three to six times what the city next door costs, and pricing
them at the city fee would lose money on every order.

So each served emirate is clipped to a circle around the kitchen, sized to the
area the rate card was actually measured over:

  * Sharjah    25 km — covers Al Khan through University City (17 road km),
                       excludes Al Dhaid (~55 km) and the east coast exclaves.
  * Ajman      30 km — covers the whole coastal emirate including Emirates
                       City, excludes the Masfout and Manama exclaves (~50 km).
  * Dubai      40 km — covers Deira through Palm Jumeirah and Dubai Marina,
                       the furthest points the POC quoted (48 road km / AED 51),
                       and stops short of Jebel Ali and Hatta.

The remainder of every emirate keeps the 50 AED third-party price, and is a
genuine remainder: the served circle is punched out of it as a hole, so the two
zones do not overlap. That could have been left to `display_order` — list the
city first and take the first match — but a shape whose price depends on the
row above it is a shape you cannot look at and understand. It also draws wrong:
two translucent fills stacked over Deira, and no way to tell which one an order
there is actually paying.

The hole is the whole circle, not the circle clipped to the emirate. Anywhere
the two disagree is already outside the outer ring, and a point outside the
outline is excluded before holes are ever consulted.

Distances are computed on a local equirectangular approximation, which is
accurate to well under a percent at these radii, and the clip region is a
96-gon so the boundary is smooth at street scale.

Run from apps/api:

    python -m scripts.build_delivery_zones

Writes app/data/uae_delivery_zones.geojson.json, which the
`050_delivery_zone_fulfilment` migration seeds from. Regenerate and commit the
output; nothing computes this at runtime.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Sequence

Ring = list[tuple[float, float]]

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
SOURCE = DATA / "uae_emirates.geojson.json"
TARGET = DATA / "uae_delivery_zones.geojson.json"

# Melting Moments Cakes, Al Qasimia, Sharjah — every run starts here.
ORIGIN_LAT = 25.3304139
ORIGIN_LNG = 55.3710382

KM_PER_DEG_LAT = 111.32
CIRCLE_POINTS = 96

# emirate -> radius in km of the served city zone
SERVED_RADIUS_KM: dict[str, float] = {
    "Sharjah": 25.0,
    "Ajman": 30.0,
    "Dubai": 40.0,
}


def _circle(lat: float, lng: float, radius_km: float) -> Ring:
    """A ground circle rendered as a convex ring in [lng, lat] degrees."""
    d_lat = radius_km / KM_PER_DEG_LAT
    d_lng = radius_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return [
        (
            lng + d_lng * math.cos(2 * math.pi * i / CIRCLE_POINTS),
            lat + d_lat * math.sin(2 * math.pi * i / CIRCLE_POINTS),
        )
        for i in range(CIRCLE_POINTS)
    ]


def _is_inside(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """Left of the directed edge a→b, for a counter-clockwise clip ring."""
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= 0


def _intersect(
    p: tuple[float, float],
    q: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    x1, y1 = p
    x2, y2 = q
    x3, y3 = a
    x4, y4 = b
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denom == 0:
        return q
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def clip_to_convex(subject: Sequence[tuple[float, float]], clip: Ring) -> Ring:
    """
    Sutherland–Hodgman. Valid because the clip ring is convex — the subject
    need not be.
    """
    output: Ring = list(subject)
    for i in range(len(clip)):
        if not output:
            return []
        a, b = clip[i], clip[(i + 1) % len(clip)]
        current, output = output, []
        prev = current[-1]
        for point in current:
            if _is_inside(point, a, b):
                if not _is_inside(prev, a, b):
                    output.append(_intersect(prev, point, a, b))
                output.append(point)
            elif _is_inside(prev, a, b):
                output.append(_intersect(prev, point, a, b))
            prev = point
    return output


def _parts(geometry: dict) -> list[list[Ring]]:
    if geometry["type"] == "MultiPolygon":
        return [
            [[tuple(c) for c in ring] for ring in poly]
            for poly in geometry["coordinates"]
        ]
    return [[[tuple(c) for c in ring] for ring in geometry["coordinates"]]]


def _ring_area(ring: Sequence[tuple[float, float]]) -> float:
    """Twice the signed shoelace area — only ever compared against zero."""
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return abs(total)


def _ccw(ring: Ring) -> Ring:
    signed = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        signed += x1 * y2 - x2 * y1
    return ring if signed >= 0 else ring[::-1]


def clip_geometry(geometry: dict, radius_km: float) -> dict | None:
    """The part of `geometry` within `radius_km` of the kitchen."""
    clip = _ccw(_circle(ORIGIN_LAT, ORIGIN_LNG, radius_km))
    kept: list[list[Ring]] = []
    for part in _parts(geometry):
        if len(part) > 1:
            raise ValueError("source outlines are expected to have no holes")
        clipped = clip_to_convex(part[0], clip)
        # A polygon needs three distinct corners; anything smaller is a sliver
        # the clipper produced where the circle grazed the outline.
        if len(clipped) >= 3 and _ring_area(clipped) > 1e-9:
            kept.append([[list(pt) for pt in clipped]])
    if not kept:
        return None
    return {"type": "MultiPolygon", "coordinates": kept}


def _flat(geometry: dict) -> dict:
    """Normalise a Polygon to a MultiPolygon so every zone has one shape."""
    if geometry["type"] == "MultiPolygon":
        return geometry
    return {"type": "MultiPolygon", "coordinates": [geometry["coordinates"]]}


def punch_out(geometry: dict, radius_km: float) -> dict:
    """
    The emirate with its served city removed, as a hole in every part.

    Leaves the two zones disjoint, so which fee applies is a property of where
    the pin is rather than of which row was checked first.
    """
    hole = [list(pt) for pt in _circle(ORIGIN_LAT, ORIGIN_LNG, radius_km)]
    hole.append(hole[0])
    return {
        "type": "MultiPolygon",
        "coordinates": [[*part, hole] for part in _parts(geometry)],
    }


def _count(geometry: dict) -> tuple[int, int]:
    polys = geometry["coordinates"]
    return len(polys), sum(len(ring) for poly in polys for ring in poly)


def build() -> list[dict]:
    emirates = json.loads(SOURCE.read_text())
    missing = set(SERVED_RADIUS_KM) - set(emirates)
    if missing:
        raise ValueError(f"no outline for {sorted(missing)}")

    zones: list[dict] = []
    for emirate, radius in SERVED_RADIUS_KM.items():
        clipped = clip_geometry(emirates[emirate], radius)
        if clipped is None:
            raise ValueError(f"{emirate} has nothing within {radius} km of the kitchen")
        zones.append(
            {
                "name": f"{emirate} City",
                "radius_km": radius,
                "geometry": clipped,
            }
        )
    for emirate, geometry in emirates.items():
        radius = SERVED_RADIUS_KM.get(emirate)
        zones.append(
            {
                "name": emirate,
                "geometry": (
                    punch_out(geometry, radius)
                    if radius is not None
                    else _flat(geometry)
                ),
            }
        )
    return zones


def main() -> None:
    zones = build()
    TARGET.write_text(json.dumps(zones, separators=(",", ":")) + "\n")
    for zone in zones:
        parts, points = _count(zone["geometry"])
        radius = zone.get("radius_km")
        suffix = f"  (clipped to {radius:.0f} km)" if radius else ""
        print(f"{zone['name']:<22} parts={parts:<4} points={points}{suffix}")
    print(f"\nwrote {TARGET.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
