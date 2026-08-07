"""
Cut the emirate outlines into the zones the courier fee strategy actually prices.

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

Sharjah is then cut once more, because a second courier is cheaper inside part
of it. noon Send charges AED 12 flat to 10 road km on a bike, then +1/km to 15
and +1.50/km to 20, against Lalamove's `17 + 0.70/km` — cheaper at every
distance it will carry, surge included. Price therefore never decides the
boundary; noon Send's **20 km ceiling on pickup-to-drop-off distance** does.
Road distance runs about 1.49x straight line across the sixteen Sharjah areas
the rate card was measured over, so 20 road km is a 13.4 km circle:

  * Sharjah Central  13.4 km — Al Khan through Al Zahia and University City, on
                               noon Send. Al Rahmaniya (21.1 road km) is over
                               the ceiling and stays on Lalamove, as does
                               everything beyond it.

That the boundary is a circle around the kitchen rather than a road-distance
isoline is the approximation being made here, and it is the conservative one:
1.49x is the mean ratio, so a few pins just inside the circle will be a little
over 20 road km. noon Send refuses those outright, and `courier_service` falls
back to Lalamove when it does — the customer's fee never moves either way.

The remainder of every emirate keeps the 50 AED third-party price, and is a
genuine remainder: the served circle is punched out of it as a hole, so the two
zones do not overlap. Sharjah City is punched the same way by the Central
circle. That could have been left to `display_order` — list the smaller zone
first and take the first match — but a shape whose price depends on the row
above it is a shape you cannot look at and understand. It also draws wrong: two
translucent fills stacked over Deira, and no way to tell which one an order
there is actually paying.

The hole is the whole circle, not the circle clipped to the emirate. Anywhere
the two disagree is already outside the outer ring, and a point outside the
outline is excluded before holes are ever consulted.

Distances are computed on a local equirectangular approximation, which is
accurate to well under a percent at these radii, and the clip region is a
96-gon so the boundary is smooth at street scale.

Run from apps/api:

    python -m scripts.build_delivery_zones

Writes app/data/uae_delivery_zones.geojson.json, which the delivery-zone
migrations seed from. Regenerate and commit the output; nothing computes this
at runtime.
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
ORIGIN_LNG = 55.3736131

KM_PER_DEG_LAT = 111.32
CIRCLE_POINTS = 96

#: Concentric bands per emirate, inner to outer, as (zone name, radius km from
#: the kitchen). Each band is clipped to its own radius and has the band inside
#: it punched out, so the set is disjoint and a pin's price is a property of
#: where it is rather than of which row was checked first.
#:
#: Radii are crow-flies. Road distance runs about **1.42x** straight line across
#: the 88 areas the Lalamove rate card was measured over, so a road figure
#: divided by 1.42 gives the circle that contains it.
#:
#: Why each boundary is where it is. Every figure below is the **measured**
#: crow distance of the furthest area that band should contain, taken from the
#: 88 areas quoted live against the Lalamove API — not a road figure divided by
#: a detour factor. The factor varies enough per route that estimating from it
#: put three bands in the wrong place, Jebel Ali among them.
#:
#:  Sharjah Central 13.4 km — noon Send's 20 km road ceiling. Reach decides this
#:                            one, not price. University City (12.4) is in;
#:                            Al Rahmaniya (15.9) is out, correctly — noon Send
#:                            will not carry it.
#:  Sharjah Outer     25 km — Al Rahmaniya and the rest of the city. The east
#:                            coast starts at 80 km and stays third-party.
#:  Ajman City        22 km — the whole coastal emirate; the furthest measured
#:                            area is Emirates City at 16.7. Masfout (89.5) is out.
#:  Dubai Near        20 km — Al Nahda (4.9) through Business Bay (19.6).
#:                            Courier cost 24-37.
#:  Dubai Mid         31 km — Silicon Oasis (23.2) through Al Barsha (30.2).
#:                            Cost 41-44.
#:  Dubai Far         48 km — Palm Jumeirah (33.9) through Jebel Ali (47.0).
#:                            Cost 47-59. The 40 km first cut dropped Investment
#:                            Park and Jebel Ali onto the third party at 80,
#:                            when a Lalamove car reaches both for 56-59.
#:  Umm al-Quwain     36 km — all five measured areas (30.4-35.1), where a car
#:                            costs 43-47 against the flat 80. Falaj Al Mualla
#:                            (55) is out; Lalamove refuses it anyway.
#:  Ras al-Khaimah    78 km — Al Jazirah Al Hamra (55.6) through RAK City
#:                            (76.7), where a car costs 63-80. Al Rams (91) is
#:                            out, correctly: a car costs 94 there.
#:
#: The three Dubai bands all carry the same fee today. They are drawn anyway,
#: because the whole point of the rebuild is that repricing the far half later
#: is an admin edit rather than a migration that redraws the map.
#: Bands that close gaps in the source outlines rather than inheriting them.
#:
#: All of them. The construction is `circle ∩ grown-emirate − neighbours`, and
#: the growth is bounded to `GAP_FILL_KM`, so a band can only pick up ground its
#: own emirate very nearly claims already. An earlier version subtracted
#: neighbours from the bare circle with no bound and claimed open sea; that is
#: what `GAP_FILL_KM` exists to prevent.
FILL_GAPS: frozenset[str] = frozenset(name for bands in () for name, _ in bands) | {
    "Sharjah Central",
    "Sharjah Outer",
    "Ajman City",
    "Dubai Near",
    "Dubai Mid",
    "Dubai Far",
    "Umm al-Quwain City",
    "Ras al-Khaimah City",
}

BANDS: dict[str, list[tuple[str, float]]] = {
    "Sharjah": [("Sharjah Central", 13.4), ("Sharjah Outer", 25.0)],
    "Ajman": [("Ajman City", 22.0)],
    "Dubai": [("Dubai Near", 20.0), ("Dubai Mid", 31.0), ("Dubai Far", 48.0)],
    "Umm al-Quwain": [("Umm al-Quwain City", 36.0)],
    "Ras al-Khaimah": [("Ras al-Khaimah City", 78.0)],
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


def _count(geometry: dict) -> tuple[int, int, int]:
    polys = geometry["coordinates"]
    return (
        len(polys),
        sum(len(poly) - 1 for poly in polys),
        sum(len(ring) for poly in polys for ring in poly),
    )


def _shapely():
    """Imported lazily so the module still loads without the `geo` extra."""
    try:
        from shapely import make_valid  # noqa: PLC0415
        from shapely.geometry import mapping, shape  # noqa: PLC0415
        from shapely.ops import unary_union  # noqa: PLC0415
    except ModuleNotFoundError as exc:  # pragma: no cover - developer setup
        raise SystemExit(
            "Redrawing the delivery map needs shapely:\n\n"
            '    pip install -e ".[geo]"\n\n'
            "It is a build-time dependency only — the generated geometry is "
            "committed, and nothing at runtime imports it."
        ) from exc
    return make_valid, mapping, shape, unary_union


def _as_polygon(geometry: dict, shape, make_valid):
    """A source outline as one valid shapely geometry.

    `make_valid` is not defensive dressing: the raw outlines self-intersect, and
    a plain `unary_union` over them dies with a side-location conflict at
    55.5048, 25.5001.
    """
    if geometry["type"] == "MultiPolygon":
        return make_valid(shape(geometry))
    return make_valid(
        shape({"type": "Polygon", "coordinates": geometry["coordinates"]})
    )


#: How far past its own outline an emirate may claim unclaimed ground, in km —
#: and how close a neighbour has to be to claim it instead.
#:
#: The gaps being closed are surveying slop, not territory: Al Taawun sits 0.13
#: km outside the Sharjah outline in Khalid Lagoon. The thing that must stay
#: out is open sea on the Dubai side, and the nearest such point is 10.8 km
#: from Sharjah. One kilometre sits an order of magnitude clear of both, so the
#: figure is not delicately balanced and does not need to be.
GAP_FILL_KM = 1.0


def served_band(
    emirate: str,
    emirates: dict,
    radius_km: float,
    inner_radius_km: float | None,
) -> dict:
    """
    The band, with gaps in the source outlines closed in the emirate's favour.

    Built as `circle ∩ slightly-grown-emirate − neighbours` rather than
    `emirate ∩ circle`. The difference is addresses the surveyor left out: gaps
    between coastline parts belong to no emirate at all, so the old form left
    them belonging to no *zone* either. Al Taawun is one of them, 1.6 km from
    the kitchen, and it was falling past every polygon to a live courier quote
    instead of being delivered on the fee its neighbours pay.

    The growth is what keeps this honest. An earlier attempt subtracted the
    neighbours from the bare circle, which claims everything nobody else claims
    — including open water on the Dubai side, where an order would be offered to
    noon Send, refused for crossing an emirate, fall back to Lalamove and be
    charged this zone's fee of zero. Bounded to a kilometre, the fill reaches
    the lagoon and nothing else.

    Neighbours are subtracted last, so where the grown outline overlaps a real
    emirate, the real emirate wins.
    """
    make_valid, mapping, shape, unary_union = _shapely()

    circle = _as_polygon(
        {
            "type": "Polygon",
            "coordinates": [
                [list(pt) for pt in _circle(ORIGIN_LAT, ORIGIN_LNG, radius_km)]
            ],
        },
        shape,
        make_valid,
    )
    home = _as_polygon(emirates[emirate], shape, make_valid)
    neighbours = unary_union(
        [_as_polygon(g, shape, make_valid) for k, g in emirates.items() if k != emirate]
    )

    # Real territory first, and it is never given away: anything genuinely
    # inside the emirate belongs to this band whatever sits next to it.
    inside = circle.intersection(home)

    # Then the gaps, which are the ground no outline claims. A gap joins this
    # band only if home is the *nearest* emirate to it — within `GAP_FILL_KM`
    # of home and no closer than that to anyone else.
    #
    # The second half is what a plain buffer misses. 25.3008, 55.3529 is a gap
    # on the Sharjah-Dubai line, 0.61 km from Sharjah but 0.19 km from Dubai;
    # a buffer of home alone swallows it, and since Sharjah's inner band is
    # noon Send's, an order there would be refused for crossing an emirate and
    # then carried by a Lalamove car for a fee of zero.
    margin = GAP_FILL_KM / KM_PER_DEG_LAT
    gaps = (
        circle.intersection(home.buffer(margin))
        .difference(home)
        .difference(neighbours.buffer(margin))
    )

    band = inside.union(gaps).difference(neighbours)

    if inner_radius_km is not None:
        inner = _as_polygon(
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        list(pt)
                        for pt in _circle(ORIGIN_LAT, ORIGIN_LNG, inner_radius_km)
                    ]
                ],
            },
            shape,
            make_valid,
        )
        band = band.difference(inner)

    if band.is_empty:
        raise ValueError(f"{emirate} has nothing within {radius_km} km of the kitchen")

    geometry = mapping(band)
    if geometry["type"] == "Polygon":
        geometry = {"type": "MultiPolygon", "coordinates": [geometry["coordinates"]]}
    return {
        "type": "MultiPolygon",
        "coordinates": [
            [[list(pt) for pt in ring] for ring in poly]
            for poly in geometry["coordinates"]
        ],
    }


def build() -> list[dict]:
    emirates = json.loads(SOURCE.read_text())
    missing = set(BANDS) - set(emirates)
    if missing:
        raise ValueError(f"no outline for {sorted(missing)}")

    zones: list[dict] = []
    for emirate, bands in BANDS.items():
        radii = [r for _, r in bands]
        if radii != sorted(radii):
            raise ValueError(f"{emirate} bands must run inner to outer: {radii}")

        for index, (name, radius) in enumerate(bands):
            inner = bands[index - 1][1] if index > 0 else None
            if FILL_GAPS is None or name in FILL_GAPS:
                shape = served_band(emirate, emirates, radius, inner)
            else:
                shape = clip_geometry(emirates[emirate], radius)
                if shape is None:
                    raise ValueError(
                        f"{emirate} has nothing within {radius} km of the kitchen"
                    )
                if inner is not None:
                    shape = punch_out(shape, inner)
            zones.append({"name": name, "radius_km": radius, "geometry": shape})

    # Sorted by radius, and the order is load-bearing.
    #
    # Every band is a circle around the same kitchen, so the bands of different
    # emirates are concentric and nest inside one another. What keeps them apart
    # is each carrying its neighbours as holes — which works everywhere the
    # outlines actually claim the ground, and not in the gaps between them.
    # Khalid Lagoon is claimed by nobody, so every circle wide enough to reach it
    # contains it.
    #
    # Smallest first, and `find_zone` takes the first match, so a gap belongs to
    # the nearest band that reaches it. The lagoon goes to Sharjah Central at
    # 13.4 km rather than to Ras al-Khaimah at 70, which is both the right answer
    # and the only one anybody would guess.
    #
    # This is a deliberate reversal. The zones used to be disjoint by
    # construction and the note here said precedence was worth avoiding, because
    # a shape whose price depends on the row above it is hard to reason about.
    # That held while a gap in an outline merely meant a hole in the map; it
    # stopped holding once one of those holes turned out to be 1.6 km from the
    # kitchen with customers in it.
    zones.sort(key=lambda z: z["radius_km"])

    for emirate, geometry in emirates.items():
        bands = BANDS.get(emirate)
        zones.append(
            {
                "name": emirate,
                "geometry": (
                    punch_out(geometry, bands[-1][1])
                    if bands is not None
                    else _flat(geometry)
                ),
            }
        )
    return zones


def main() -> None:
    zones = build()
    TARGET.write_text(json.dumps(zones, separators=(",", ":")) + "\n")
    for zone in zones:
        parts, holes, points = _count(zone["geometry"])
        radius = zone.get("radius_km")
        suffix = f"  (clipped to {radius:.0f} km)" if radius else ""
        print(
            f"{zone['name']:<22} parts={parts:<4} holes={holes:<4} "
            f"points={points}{suffix}"
        )
    print(f"\nwrote {TARGET.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
