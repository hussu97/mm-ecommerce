"""
Cut the emirate outlines into the zones the courier fee strategy actually prices.

The strategy is not "per emirate". It is "per what a courier run costs from the
Sharjah kitchen", and an emirate is a poor proxy for that: Sharjah reaches Al
Dhaid and Khor Fakkan, Dubai reaches Hatta, Ajman owns two inland exclaves.
Those places cost three to six times what the city next door costs, and pricing
them at the city fee would lose money on every order.

So each served emirate is cut into concentric bands around the kitchen, sized
to the areas the rate cards were actually measured over, and its leftover is
whatever the bands did not reach.

The bands themselves are `BANDS` below, with the reasoning for each radius
beside it. Two rules govern the set rather than any one entry:

  * **A band exists wherever one price would otherwise span two very different
    runs.** Ras al-Khaimah was a single 78 km band charging 50 AED flat from 56
    km out to 77; Sharjah's leftover held Al Dhaid at 25 km and Khor Fakkan at
    98 in one shape. Splitting them changes no fee today and makes repricing
    the far half an admin edit tomorrow.
  * **The leftover must be a place, not an accident.** Because the leftover is
    "the emirate minus the bands", it inherits that emirate's exclaves. Once
    the bands reach far enough inland, Sharjah's leftover *is* the east coast,
    Ajman's *is* Masfout, Dubai's *is* Hatta — which is why they are renamed
    rather than redrawn. Radial bands cannot separate Sharjah's east-coast
    towns from Fujairah's; both sit 96-103 km out. The existing
    `circle ∩ emirate − neighbours` machinery already separates them by
    outline, so this is a band change plus a rename and nothing more.

The leftover keeps the third-party price, and is a genuine remainder: the
outermost band's circle is punched out of it as a hole, so the two shapes do
not overlap. Each band is punched the same way by the one inside it. That could
have been left to `display_order` — list the smaller zone first and take the
first match — but a shape whose price depends on the row above it is a shape
you cannot look at and understand. It also draws wrong: two translucent fills
stacked over Deira, and no way to tell which one an order there is actually
paying.

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
#: Radii are crow-flies, and every figure below is a **measured** crow distance
#: taken from the areas quoted live against a courier API — not a road figure
#: divided by a detour factor. The factor varies enough per route that
#: estimating from it put three bands in the wrong place, Jebel Ali among them.
#:
#: Why each boundary is where it is.
#:
#:  Sharjah Core     3.5 km — Al Majaz, Al Khan, Al Qasimia, Al Taawun, Abu
#:                            Shagara, Al Layyah, Rolla: the set where Slider's
#:                            car beats noon Send's bike. Two areas inside the
#:                            radius lose to noon Send — Al Nud and Sharjah
#:                            Industrial Area, both 2.9 km straight and 5-7 km
#:                            by road — and a radial band cannot exclude them.
#:                            The net is about AED 1 an order; the reason to
#:                            draw it is dispatch reliability, not the money.
#:  Sharjah Central 13.4 km — noon Send's 20 km road ceiling. Reach decides this
#:                            one, not price. University City (12.4) is in;
#:                            Al Rahmaniya (15.9) is out, correctly — noon Send
#:                            will not carry it.
#:  Sharjah Outer     25 km — Al Rahmaniya and the rest of the city.
#:  Sharjah Inland    60 km — Al Dhaid and the inland desert, 25-60 km out. It
#:                            used to sit in the `Sharjah` leftover beside Khor
#:                            Fakkan at 98 km, which is a 73 km spread inside
#:                            one shape and one price.
#:  Ajman City        22 km — the whole coastal emirate; the furthest measured
#:                            area is Emirates City at 16.7. Masfout (89.5) is out.
#:  Ajman Inland      60 km — the same split, for the same reason: Masfout is an
#:                            exclave near Hatta and does not belong in a band
#:                            with the corniche.
#:  Dubai Near        20 km — Al Nahda (4.9) through Business Bay (19.6).
#:                            Courier cost 24-37.
#:  Dubai Mid         31 km — Silicon Oasis (23.2) through Al Barsha (30.2).
#:                            Cost 41-44.
#:  Dubai Far         48 km — Palm Jumeirah (33.9) through Jebel Ali (47.0).
#:                            Cost 47-59. The 40 km first cut dropped Investment
#:                            Park and Jebel Ali onto the third party at 80,
#:                            when a Lalamove car reaches both for 56-59.
#:  Dubai Outer       70 km — the southern desert. Stops well short of Hatta
#:                            (96), which is the exclave the leftover now holds
#:                            on its own.
#:  Umm al-Quwain     36 km — all five measured areas (30.4-35.1), where a car
#:                            costs 43-47 against the flat 80. Falaj Al Mualla
#:                            (55) is out; Lalamove refuses it anyway.
#:  Umm al-Quwain     60 km — Falaj Al Mualla and the inland strip, which was
#:  Inland                    otherwise sandwiched between the UAQ City band at
#:                            36 km and the Ras al-Khaimah band at 78.
#:  Ras al-Khaimah    60 km — Al Jazirah Al Hamra (55.6) and everything south of
#:  South                     it. One 78 km band charged 50 AED flat from 56 km
#:                            to 77; three bands can be repriced separately.
#:  Ras al-Khaimah    80 km — RAK City (76.7), where a car costs 63-80.
#:  City
#:  Ras al-Khaimah   110 km — Al Rams (91) and the northern tip. A car costs 94
#:  North                     there, so this band is where a future reprice will
#:                            land first.
#:
#: Bands sharing a fee are drawn anyway, because the whole point of the rebuild
#: is that repricing the far half later is an admin edit rather than a migration
#: that redraws the map.
BANDS: dict[str, list[tuple[str, float]]] = {
    "Sharjah": [
        ("Sharjah Core", 3.5),
        ("Sharjah Central", 13.4),
        ("Sharjah Outer", 25.0),
        ("Sharjah Inland", 60.0),
    ],
    "Ajman": [("Ajman City", 22.0), ("Ajman Inland", 60.0)],
    "Dubai": [
        ("Dubai Near", 20.0),
        ("Dubai Mid", 31.0),
        ("Dubai Far", 48.0),
        ("Dubai Outer", 70.0),
    ],
    "Umm al-Quwain": [
        ("Umm al-Quwain City", 36.0),
        ("Umm al-Quwain Inland", 60.0),
    ],
    "Ras al-Khaimah": [
        ("Ras al-Khaimah South", 60.0),
        ("Ras al-Khaimah City", 80.0),
        ("Ras al-Khaimah North", 110.0),
    ],
}

#: What an emirate's leftover is called once the bands above have taken the near
#: ground. Only the three whose leftover is a *named place* rather than "the
#: rest of the emirate" are renamed, and the name is the point: `Sharjah` used
#: to hold Al Dhaid at 25 km and Khor Fakkan at 98 in one shape, and nothing on
#: screen said so.
#:
#: Every name here still begins with an emirate, because
#: `delivery_service.public_zone_name` maps a zone to its emirate by prefix and
#: anything else leaks the raw band name to a customer's browser.
REMAINDER_NAMES: dict[str, str] = {
    "Sharjah": "Sharjah East Coast",
    "Ajman": "Ajman Masfout",
    "Dubai": "Dubai Hatta",
}

#: Bands that close gaps in the source outlines rather than inheriting them.
#:
#: All of them, and derived rather than listed. The construction is
#: `circle ∩ grown-emirate − neighbours` with the growth bounded to
#: `GAP_FILL_KM`, so a band can only pick up ground its own emirate very nearly
#: claims already. An earlier version subtracted neighbours from the bare circle
#: with no bound and claimed open sea; that is what `GAP_FILL_KM` exists to
#: prevent.
#:
#: This was a hand-kept list beside `BANDS`, which meant adding a band and
#: forgetting to add its name fell silently back to the old `emirate ∩ circle`
#: form — the one that left Al Taawun belonging to no zone at all.
FILL_GAPS: frozenset[str] = frozenset(
    name for bands in BANDS.values() for name, _ in bands
)


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

    # The leftover, which is the emirate with everything the bands took punched
    # out of it. Named for what it actually holds where that is a place rather
    # than a direction — `Sharjah East Coast` rather than a second `Sharjah`
    # sitting under the four bands of the same name.
    for emirate, geometry in emirates.items():
        bands = BANDS.get(emirate)
        zones.append(
            {
                "name": REMAINDER_NAMES.get(emirate, emirate),
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
