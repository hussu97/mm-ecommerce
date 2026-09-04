"""
Draw the per-area delivery map: one polygon per named area, coloured by the
courier a run from the Sharjah kitchen is cheapest on.

The old map (`build_delivery_zones.py`) cut each emirate into concentric cost
bands around the kitchen — a couple of dozen shapes. This one is finer: it takes
the ~97 areas the fare survey actually priced (`app/data/uae_delivery_areas.json`,
centroids lifted from the `slider-poc` analysis) and gives each its own polygon,
so a courier and a fee can be argued per area rather than per band.

**Geometry is a per-emirate Voronoi, made compact and single-piece.** Each area
is a point; within an emirate the points tessellate into cells (every spot
belongs to its nearest area), and each cell is clipped to that emirate's outline
(per-emirate, not one national diagram, so a cell stays in the emirate that owns
it — what noon Send's "same emirate" rule needs). Then three passes fix the two
faults a raw Voronoi has here:

  * **One connected piece.** A cell clipped to a non-contiguous emirate (Sharjah's
    east-coast enclaves, Abu Dhabi's islands) or pierced by a neighbour's
    territory comes back as scattered fragments — a single "zone" appearing in
    two places 100 km apart. We keep only the piece containing the area's own
    point and drop the rest.
  * **A capped reach.** No cell may extend further than `RADIUS_KM` from its
    point. Around the kitchen the points are dense and the cap never bites; out
    in Abu Dhabi's desert it stops one point's cell from swallowing 200 km of
    sand. Ground beyond every cell's cap is simply unserviceable — which for
    empty desert is the honest answer, not a giant third-party polygon.
  * **Gaps filled locally.** A dropped fragment or a sliver between cells is
    absorbed into the neighbouring cell it shares the longest border with (same
    emirate only), so the served ground stays gap-free without any cell reaching
    across the country.

The outlines are `app/data/uae_emirates.geojson.json`.

**The fee is inherited, the courier is re-derived.** The shop's fees are kept as
they are today: each area takes the `delivery_fee` / threshold of whichever
current (v2) band its centroid falls in (`V2_ZONES` below, matched by
point-in-polygon over the committed v2 geometry). The *courier* is chosen fresh
from the Sharjah-branch cost survey (`app/data/courier_costs.json`):

  * A fee at or above the outer tier (`>= 80`) is third party, as it is today —
    the far, uneconomical ground.
  * Otherwise the cheapest serviceable courier wins, with one reliability
    thumb on the scale: Lalamove beats Slider only when it is cheaper by **more
    than AED 3**, because Slider is the steadier of the two. noon Send is taken
    purely on price where it can serve (inside Sharjah, one emirate, <= 20 km).
  * When Slider wins, the polygon names the *tier* it may actually be run on.
    **A bike only rides where it can reach the kitchen without leaving its land.**
    That is stricter than "same emirate": Sharjah owns the east coast (Kalba,
    Khor Fakkan) and a strip north of Ajman, but a bike reaching either has to
    cross another emirate, so those are car even though they are Sharjah. Bike is
    allowed only on an area in the **same contiguous Sharjah landmass as the
    kitchen** (`_bike_reachable`). Everywhere else Slider is a car. So the contest
    outside the kitchen block is car vs Lalamove; inside it is bike (or car) vs
    Lalamove vs noon Send.

Every polygon is dispatched on its own — there is no batching. Lalamove books
directly, one order at a time.

Run from apps/api:

    python -m scripts.build_delivery_areas

Writes:
  * app/data/uae_delivery_zones.v7.geojson.json — name -> geometry.
  * app/data/uae_delivery_areas_assignments.v4.json — name -> fee, threshold,
    provider; the map migration reads it for everything but shape.
Earlier vX files that shipped migrations froze are left untouched.

Regenerate and commit both. Nothing computes this at runtime. Slider's fares in
`courier_costs.json` are a production probe from the whitelisted VM egress; the
Lalamove figures beside them are the market survey and noon Send's are its rate
card. Areas with no cost entry (far inland/desert points) fall to `third_party`
on their outer-tier fee, which needs no fare. Re-probe and rebuild when fares
move (`scripts/probe_courier_fares.py`).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
AREAS = DATA / "uae_delivery_areas.json"
COSTS = DATA / "courier_costs.json"
EMIRATES = DATA / "uae_emirates.geojson.json"
V2_GEOMETRY = DATA / "uae_delivery_zones.geojson.json"
OUT_GEOMETRY = DATA / "uae_delivery_zones.v7.geojson.json"
OUT_ASSIGN = DATA / "uae_delivery_areas_assignments.v4.json"

#: The kitchen's emirate and its pin. A Slider bike only rides where it can reach
#: the kitchen without crossing another emirate — the kitchen's own contiguous
#: Sharjah landmass, computed in `_bike_reachable`.
KITCHEN_EMIRATE = "Sharjah"
KITCHEN_LAT = 25.3304139
KITCHEN_LNG = 55.3710382

#: No cell may reach further than this (km) from its own point. Around the
#: kitchen the points are dense and it never bites; in the far desert it stops a
#: cell swallowing empty sand, leaving that ground unserviceable rather than a
#: giant third-party polygon.
RADIUS_KM = 40.0

#: The emirate name the areas file uses -> the canonical name the outlines and
#: `delivery_service.public_zone_name` use. A polygon's name must begin with the
#: canonical emirate, because that prefix is how a zone is mapped to its emirate
#: for the customer and for Slider's vehicle rule.
CANONICAL = {
    "Sharjah": "Sharjah",
    "Ajman": "Ajman",
    "Dubai": "Dubai",
    "Fujairah": "Fujairah",
    "Abu Dhabi": "Abu Dhabi",
    "Umm Al Quwain": "Umm al-Quwain",
    "Ras Al Khaimah": "Ras al-Khaimah",
}

#: The current (v2) bands in `display_order`, name -> (fee, threshold, free). An
#: area inherits the fee of the first band its centroid falls in — matched in
#: this order, exactly as `find_zone` matches at runtime. Kept in step with
#: migration `126_cost_banded_map_v2`; a test would catch drift, but the fees are
#: the shop's and change rarely.
V2_ZONES: list[tuple[str, str, str, bool]] = [
    ("Sharjah Core", "0.00", "0.00", True),
    ("Sharjah Central", "0.00", "0.00", True),
    ("Dubai Near", "20.00", "75.00", True),
    ("Ajman City", "10.00", "75.00", True),
    ("Sharjah Outer", "20.00", "75.00", True),
    ("Dubai Mid", "20.00", "75.00", True),
    ("Umm al-Quwain City", "30.00", "75.00", True),
    ("Dubai Far", "20.00", "75.00", True),
    ("Sharjah Inland", "80.00", "200.00", True),
    ("Ajman Inland", "80.00", "200.00", True),
    ("Umm al-Quwain Inland", "80.00", "200.00", True),
    ("Ras al-Khaimah South", "50.00", "100.00", True),
    ("Dubai Outer", "80.00", "200.00", True),
    ("Ras al-Khaimah City", "50.00", "100.00", True),
    ("Ras al-Khaimah North", "50.00", "100.00", True),
    ("Abu Dhabi", "80.00", "200.00", True),
    ("Ajman Masfout", "80.00", "200.00", True),
    ("Dubai Hatta", "80.00", "200.00", True),
    ("Fujairah", "80.00", "200.00", True),
    ("Ras al-Khaimah", "80.00", "200.00", True),
    ("Sharjah East Coast", "80.00", "200.00", True),
    ("Umm al-Quwain", "80.00", "200.00", True),
]

#: What an area gets when its centroid falls in no v2 band at all — the outer
#: tier, which is where an unmapped pin belongs.
OUTER = ("80.00", "200.00", True)

#: The reliability margin: Lalamove has to be cheaper than Slider by more than
#: this to win, because Slider is the steadier courier.
SLIDER_MARGIN = Decimal("3")

#: A fee at or above this is third party — the far ground no courier is run to.
THIRD_PARTY_FEE = Decimal("80")

#: preferred courier -> the couriers an order in this zone may be moved to by
#: hand. A copy of `delivery_polygon.DEFAULT_ALTERNATES` (a migration cannot
#: import the constant; the same test keeps the two in step). A Slider **bike**
#: may be upgraded to a car (one-way); a car has no Slider alternate.
ALTERNATES = {
    "lalamove": ["third_party"],
    "third_party": ["lalamove"],
    "noon_send": ["third_party", "lalamove"],
    "slider": ["lalamove", "third_party"],
    "slider_bike": ["slider_car", "lalamove", "third_party"],
    "slider_car": ["lalamove", "third_party"],
}


def _alternates(provider: str, *, bike_reachable: bool) -> list[str]:
    """The manual-move targets for this polygon.

    The per-provider default, plus noon Send for a Slider zone a bike can reach —
    the one place noon Send is a real answer. noon Send cannot cross an emirate
    boundary, so it is added only where a bike could go too (the kitchen's own
    contiguous Sharjah), never on the east coast or anywhere else.
    """
    alts = list(ALTERNATES.get(provider, []))
    if bike_reachable and provider in ("slider", "slider_bike", "slider_car"):
        alts.append("noon_send")
    return alts


#: How far past its own outline an emirate may claim unclaimed ground, in km.
#: The gaps are surveying slop, not territory — Al Taawun sits 0.13 km outside
#: the Sharjah outline in Khalid Lagoon, 1.6 km from the kitchen with customers
#: in it — so an area centroid a little over the line still belongs to its
#: emirate. Neighbours are subtracted after, so the fill only reaches ground no
#: real outline claims. Same figure and same reasoning as `build_delivery_zones`.
GAP_FILL_KM = 1.0
KM_PER_DEG = 111.32


def _shapely():
    from shapely import make_valid  # noqa: PLC0415
    from shapely.geometry import MultiPoint, Point, mapping, shape  # noqa: PLC0415
    from shapely.ops import unary_union, voronoi_diagram  # noqa: PLC0415

    return make_valid, MultiPoint, Point, mapping, shape, unary_union, voronoi_diagram


def _to_multipolygon(geom, mapping) -> dict:
    """A shapely geometry as a GeoJSON MultiPolygon, dropping non-areal parts.

    An `intersection` can yield a Polygon, a MultiPolygon, or a
    GeometryCollection with stray edges where a cell grazed the outline. Only the
    filled parts are a zone.
    """
    polys: list = []
    parts = getattr(geom, "geoms", [geom])
    for part in parts:
        gj = mapping(part)
        if gj["type"] == "Polygon":
            polys.append(gj["coordinates"])
        elif gj["type"] == "MultiPolygon":
            polys.extend(gj["coordinates"])
    if not polys:
        raise ValueError("clipped to nothing areal")
    return {"type": "MultiPolygon", "coordinates": polys}


def _keep_component(geom, point, make_valid):
    """The one connected piece of a cell that belongs to its point.

    A raw clipped cell can be several disjoint pieces (a pierced cell, a
    non-contiguous emirate). The piece that contains the point is the zone; the
    rest are somebody else's ground reached only by accident. Falls back to the
    nearest piece for the handful of border points that sit just outside their
    own outline.
    """
    if geom.is_empty:
        return geom
    parts = list(getattr(geom, "geoms", [geom]))
    if len(parts) == 1:
        return make_valid(parts[0])
    containing = [p for p in parts if p.covers(point)]
    if containing:
        return make_valid(max(containing, key=lambda p: p.area))
    return make_valid(min(parts, key=lambda p: p.distance(point)))


def _bike_reachable(areas, outline_shapes, canonical, Point) -> set[str]:
    """The area labels a bike can reach from the kitchen without leaving its land.

    Sharjah is non-contiguous — the east coast (Kalba, Khor Fakkan) and a strip
    north of Ajman are Sharjah but separated from the kitchen by another emirate,
    so a bike cannot reach them. Bike is allowed only on an area in the same
    connected piece of the Sharjah outline as the kitchen.
    """
    sharjah = outline_shapes[KITCHEN_EMIRATE]
    kitchen = Point(KITCHEN_LNG, KITCHEN_LAT)
    parts = list(getattr(sharjah, "geoms", [sharjah]))
    tol = 2.0 / KM_PER_DEG  # 2 km slack for a pin just off its own outline
    block = next((p for p in parts if p.buffer(tol).covers(kitchen)), None)
    if block is None:
        return set()
    reachable = block.buffer(tol)
    return {
        a["label"]
        for a in areas
        if canonical[a["emirate"]] == KITCHEN_EMIRATE
        and reachable.covers(Point(a["lng"], a["lat"]))
    }


def _inherit_fee(point, v2_shapes) -> tuple[str, str, bool]:
    """The fee/threshold of the first v2 band this point falls in, else outer."""
    for _name, shp, fee, threshold, free in v2_shapes:
        if shp.covers(point):
            return fee, threshold, free
    return OUTER


def _assign_provider(fee: Decimal, cost: dict, *, bike_reachable: bool) -> str:
    """The courier a run to this area is cheapest on, with the rules above.

    `bike_reachable` is whether a bike can reach this area from the kitchen
    without crossing another emirate (the kitchen's own contiguous Sharjah
    landmass — see `_bike_reachable`). A bike is only an option there; everywhere
    else the Slider option is the car, using the car fare, never the (invalid)
    bike one.
    """
    if fee >= THIRD_PARTY_FEE:
        return "third_party"

    probe_tier = (cost.get("slider_tier") or "").strip().lower()
    use_bike = bike_reachable and probe_tier == "bike" and cost.get("slider_bike")
    slider_cost = cost.get("slider_bike") if use_bike else cost.get("slider_car")
    slider_provider = "slider_bike" if use_bike else "slider_car"
    slider_ok = slider_cost is not None
    slider_cost = Decimal(str(slider_cost)) if slider_ok else None

    lala_ok = cost.get("lalamove_error") is None and cost.get("lalamove") is not None
    lala_cost = Decimal(str(cost["lalamove"])) if lala_ok else None

    noon_ok = cost.get("noon_send") is not None
    noon_cost = Decimal(str(cost["noon_send"])) if noon_ok else None

    # The fast courier: Slider unless Lalamove is cheaper by more than the margin.
    if slider_ok and lala_ok:
        if lala_cost < slider_cost - SLIDER_MARGIN:
            fast, fast_cost = "lalamove", lala_cost
        else:
            fast, fast_cost = slider_provider, slider_cost
    elif slider_ok:
        fast, fast_cost = slider_provider, slider_cost
    elif lala_ok:
        fast, fast_cost = "lalamove", lala_cost
    else:
        # No bike, no car, no Lalamove — nobody is run here. Third party, even
        # under the outer fee, because there is no courier to book.
        return "third_party"

    # noon Send on pure price where it can serve (inside Sharjah).
    if noon_ok and noon_cost <= fast_cost:
        return "noon_send"
    return fast


def build() -> tuple[list[dict], list[dict]]:
    make_valid, MultiPoint, Point, mapping, shape, unary_union, voronoi_diagram = (
        _shapely()
    )

    areas = json.loads(AREAS.read_text())["areas"]
    costs = json.loads(COSTS.read_text())["costs"]
    outlines = json.loads(EMIRATES.read_text())
    v2_raw = {z["name"]: z["geometry"] for z in json.loads(V2_GEOMETRY.read_text())}

    outline_shapes = {name: make_valid(shape(geom)) for name, geom in outlines.items()}
    margin = GAP_FILL_KM / KM_PER_DEG
    # Each emirate's claim: its outline grown a little, minus every other emirate.
    # The growth pulls in an area centroid that sits just over the line; the
    # subtraction keeps the fill from reaching ground a neighbour really owns, so
    # cells stay disjoint across emirates.
    claim_shapes = {
        name: make_valid(
            make_valid(outline.buffer(margin)).difference(
                unary_union([g for other, g in outline_shapes.items() if other != name])
            )
        )
        for name, outline in outline_shapes.items()
    }
    # v2 bands as shapes, in display order, so inheritance matches `find_zone`.
    v2_shapes = [
        (name, make_valid(shape(v2_raw[name])), fee, threshold, free)
        for name, fee, threshold, free in V2_ZONES
        if name in v2_raw
    ]

    # Group areas by canonical emirate.
    by_emirate: dict[str, list[dict]] = {}
    for area in areas:
        canonical = CANONICAL[area["emirate"]]
        by_emirate.setdefault(canonical, []).append(area)

    bike_labels = _bike_reachable(areas, outline_shapes, CANONICAL, Point)
    radius = RADIUS_KM / KM_PER_DEG

    # 1. Raw Voronoi cells per emirate, then made single-piece and reach-capped.
    cell_of: dict[str, object] = {}
    point_of: dict[str, object] = {}
    emirate_of: dict[str, str] = {}
    for emirate, members in by_emirate.items():
        claim = claim_shapes[emirate]
        pts = [(a["label"], Point(a["lng"], a["lat"])) for a in members]
        if len(pts) == 1:
            raw = {pts[0][0]: make_valid(claim)}
        else:
            mp = MultiPoint([p for _, p in pts])
            regions = list(
                voronoi_diagram(mp, envelope=claim.envelope.buffer(1.0)).geoms
            )
            raw = {}
            for label, pt in pts:
                region = next((r for r in regions if r.covers(pt)), None) or min(
                    regions, key=lambda r: r.distance(pt)
                )
                raw[label] = make_valid(region.intersection(claim))
        for area in members:
            label, name = area["label"], f"{emirate} · {area['label']}"
            point = Point(area["lng"], area["lat"])
            cell = _keep_component(raw[label], point, make_valid)
            cell = _keep_component(
                make_valid(cell.intersection(point.buffer(radius))), point, make_valid
            )
            cell_of[name], point_of[name], emirate_of[name] = cell, point, emirate

    # 2. Gap-fill within each emirate: a dropped fragment or a sliver between
    #    cells is absorbed into the neighbour it shares the most border with, so
    #    the served ground stays gap-free. Same emirate only, so nothing crosses.
    for emirate in by_emirate:
        names = [n for n in cell_of if emirate_of[n] == emirate]
        covered = unary_union([cell_of[n] for n in names])
        gap = make_valid(claim_shapes[emirate].difference(covered))
        for piece in getattr(gap, "geoms", [gap]):
            if piece.is_empty or piece.area * KM_PER_DEG**2 < 0.05:
                continue
            probe = piece.buffer(0.0005)
            best, best_len = None, 0.0
            for n in names:
                shared = probe.intersection(cell_of[n].buffer(0.0005))
                length = 0.0 if shared.is_empty else shared.length
                if length > best_len:
                    best, best_len = n, length
            if best is not None:
                cell_of[best] = make_valid(unary_union([cell_of[best], piece]))

    # 3. Re-cap after gap-fill: gap-fill can glue a far piece on, so clip every
    #    cell back inside its radius and keep the one piece with the point. Ground
    #    left over is unserviceable — the honest answer for empty desert.
    for name, point in point_of.items():
        cell_of[name] = _keep_component(
            make_valid(cell_of[name].intersection(point.buffer(radius))),
            point,
            make_valid,
        )

    # 4. Emit, in the areas file's order.
    geometry: list[dict] = []
    assignments: list[dict] = []
    for area in areas:
        emirate, label = CANONICAL[area["emirate"]], area["label"]
        name = f"{emirate} · {label}"
        cell = cell_of[name]
        if cell.is_empty:
            raise ValueError(f"{name} clipped to nothing")
        point = point_of[name]
        fee_s, threshold_s, free = _inherit_fee(point, v2_shapes)
        provider = _assign_provider(
            Decimal(fee_s), costs.get(label, {}), bike_reachable=(label in bike_labels)
        )
        geometry.append({"name": name, "geometry": _to_multipolygon(cell, mapping)})
        assignments.append(
            {
                "name": name,
                "emirate": emirate,
                "label": label,
                "lat": area["lat"],
                "lng": area["lng"],
                "delivery_fee": fee_s,
                "free_delivery_threshold": threshold_s,
                "free_delivery_eligible": free,
                "fulfilment_provider": provider,
                "alternate_providers": _alternates(
                    provider, bike_reachable=(label in bike_labels)
                ),
            }
        )

    return geometry, assignments


def main() -> None:
    geometry, assignments = build()
    OUT_GEOMETRY.write_text(json.dumps(geometry, separators=(",", ":")) + "\n")
    OUT_ASSIGN.write_text(json.dumps(assignments, indent=2) + "\n")

    import collections

    providers = collections.Counter(a["fulfilment_provider"] for a in assignments)
    print(f"{len(assignments)} polygons")
    for provider, count in sorted(providers.items()):
        print(f"  {provider:<14} {count}")
    print(f"\nwrote {OUT_GEOMETRY.relative_to(Path.cwd())}")
    print(f"wrote {OUT_ASSIGN.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
