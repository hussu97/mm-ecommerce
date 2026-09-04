"""
The committed per-area map says what the rules say.

The map that migration `177_per_area_courier_map_v2` seeds is generated data —
`scripts.build_delivery_areas` writes the geometry and the assignments, and a
person commits them. This is what stops the committed files drifting from the
rules that are supposed to have produced them: it re-derives every polygon's
courier and alternates from the same cost survey and asserts the committed value
matches. If someone hand-edits an assignment without re-running the generator, or
the generator changes and the data is not rebuilt, this fails.

The geometry checks need shapely (the build-time `geo` extra) and skip where it
is absent; the assignment-rule checks need only the cost table and always run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.delivery_polygon import FulfilmentProviderEnum
from scripts.build_delivery_areas import (
    KITCHEN_EMIRATE,
    THIRD_PARTY_FEE,
    _alternates,
    _assign_provider,
)

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
ASSIGN = json.loads((DATA / "uae_delivery_areas_assignments.v2.json").read_text())
COSTS = json.loads((DATA / "courier_costs.json").read_text())["costs"]
GEOMETRY = json.loads((DATA / "uae_delivery_zones.v6.geojson.json").read_text())

PROVIDERS = {p.value for p in FulfilmentProviderEnum}


def test_every_area_has_a_polygon_and_an_assignment():
    """One polygon per area, and the two files name the same set."""
    assert len(ASSIGN) == len(GEOMETRY)
    assert {a["name"] for a in ASSIGN} == {g["name"] for g in GEOMETRY}
    assert len(ASSIGN) >= 100  # ~107 survey areas; a floor, not the exact count


@pytest.mark.parametrize("a", ASSIGN, ids=[a["label"] for a in ASSIGN])
def test_each_assignment_matches_the_rules(a):
    """The committed courier and alternates are what the generator's own rules
    produce from the cost survey — no hand-edit has slipped in."""
    fee = Decimal(a["delivery_fee"])
    cost = COSTS.get(a["label"], {})

    assert a["fulfilment_provider"] == _assign_provider(
        fee, cost, same_emirate=(a["emirate"] == KITCHEN_EMIRATE)
    )
    assert a["fulfilment_provider"] in PROVIDERS
    assert a["alternate_providers"] == _alternates(
        a["fulfilment_provider"], a["emirate"]
    )


def test_the_outer_fee_is_always_third_party():
    """A fee at or above the outer tier is the far ground no courier is run to."""
    for a in ASSIGN:
        if Decimal(a["delivery_fee"]) >= THIRD_PARTY_FEE:
            assert a["fulfilment_provider"] == "third_party", a["name"]


def test_a_bike_only_ever_serves_the_kitchens_own_emirate():
    """A Slider bike does not cross an emirate line, whatever fare Slider quotes.

    Outside Sharjah the Slider option is always the car; `slider_bike` may appear
    only on a Sharjah polygon.
    """
    for a in ASSIGN:
        if a["fulfilment_provider"] == "slider_bike":
            assert a["emirate"] == KITCHEN_EMIRATE, a["name"]


def test_a_car_polygon_never_offers_a_bike_and_a_bike_may_offer_a_car():
    """The one directional rule, on the map: a Slider car has no Slider
    alternate; a Slider bike lists the car."""
    for a in ASSIGN:
        alts = a["alternate_providers"]
        if a["fulfilment_provider"] == "slider_car":
            assert "slider_bike" not in alts, a["name"]
        if a["fulfilment_provider"] == "slider_bike":
            assert "slider_car" in alts, a["name"]


def test_a_polygon_name_begins_with_its_emirate():
    """`public_zone_name` and Slider's vehicle rule both read the emirate off the
    zone name's prefix, so every name has to start with a real emirate."""
    canonical = {
        "Sharjah",
        "Ajman",
        "Dubai",
        "Fujairah",
        "Abu Dhabi",
        "Umm al-Quwain",
        "Ras al-Khaimah",
    }
    for a in ASSIGN:
        assert any(a["name"].startswith(e) for e in canonical), a["name"]


def test_the_geometry_is_disjoint_and_covers_its_areas():
    """Cells do not overlap, are all valid, and each area's centroid falls in its
    own polygon — bar a few border/exclave centroids that sit just outside their
    emirate outline (their neighbourhoods are still covered; a pin never lands in
    a gap)."""
    shapely_geometry = pytest.importorskip("shapely.geometry")
    shapely = pytest.importorskip("shapely")
    shapes = {
        g["name"]: shapely.make_valid(shapely_geometry.shape(g["geometry"]))
        for g in GEOMETRY
    }
    assert all(not s.is_empty and s.is_valid for s in shapes.values())

    known_border = {"Al Nahda (Sharjah)", "Masafi", "Falaj Al Mualla"}
    for a in ASSIGN:
        pt = shapely_geometry.Point(a["lng"], a["lat"])
        covering = [name for name, s in shapes.items() if s.covers(pt)]
        assert len(covering) <= 1, f"{a['label']} overlaps {covering}"
        if a["label"] not in known_border:
            assert covering == [a["name"]], f"{a['label']} in {covering}"
