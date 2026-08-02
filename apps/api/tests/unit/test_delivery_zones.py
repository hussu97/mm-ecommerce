"""
The delivery map has to put real addresses in the right emirate.

These are landmarks with coordinates anyone can check, run against the same
GeoJSON the migration seeds. If a future simplification pass or a redrawn zone
moves a boundary far enough to reprice a real neighbourhood, this notices.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.delivery_zone_service import Zone, point_in_geometry

GEOJSON = (
    Path(__file__).resolve().parents[2] / "app" / "data" / "uae_emirates.geojson.json"
)

FEES = {
    "Dubai": Decimal("35.00"),
    "Sharjah": Decimal("35.00"),
    "Ajman": Decimal("35.00"),
    "Abu Dhabi": Decimal("50.00"),
    "Ras al-Khaimah": Decimal("50.00"),
    "Fujairah": Decimal("50.00"),
    "Umm al-Quwain": Decimal("50.00"),
}


@pytest.fixture(scope="module")
def emirates() -> dict:
    return json.loads(GEOJSON.read_text())


def resolve(emirates: dict, lat: float, lng: float) -> str | None:
    for name, geometry in emirates.items():
        if point_in_geometry(lat, lng, geometry):
            return name
    return None


@pytest.mark.parametrize(
    "label,lat,lng,expected",
    [
        ("Burj Khalifa", 25.1972, 55.2744, "Dubai"),
        ("Dubai Marina", 25.0805, 55.1403, "Dubai"),
        ("Sharjah Al Majaz", 25.3213, 55.3820, "Sharjah"),
        ("Ajman Corniche", 25.4052, 55.4384, "Ajman"),
        ("Sheikh Zayed Grand Mosque", 24.4128, 54.4750, "Abu Dhabi"),
        # Al Ain has no polygon of its own — it sits inside Abu Dhabi and is
        # charged the same 50 AED, which is what the old region row did too.
        ("Al Ain Oasis", 24.2154, 55.7614, "Abu Dhabi"),
        ("Ras Al Khaimah city", 25.7895, 55.9432, "Ras al-Khaimah"),
        ("Fujairah city", 25.1288, 56.3265, "Fujairah"),
        ("Umm Al Quwain", 25.5647, 55.5532, "Umm al-Quwain"),
    ],
)
def test_landmarks_land_in_the_right_emirate(emirates, label, lat, lng, expected):
    assert resolve(emirates, lat, lng) == expected, label


def test_the_shop_itself_is_in_a_35_aed_zone(emirates):
    """Melting Moments is in Sharjah; a neighbour must not be quoted 50."""
    zone = resolve(emirates, 25.3304139, 55.3710382)
    assert zone == "Sharjah"
    assert FEES[zone] == Decimal("35.00")


def test_outside_the_country_matches_nothing(emirates):
    """Muscat is a real place, just not one we deliver to — it must fall through
    to the configured default rather than silently matching a border zone."""
    assert resolve(emirates, 23.5880, 58.3829) is None


def test_every_seeded_emirate_has_a_fee(emirates):
    assert set(emirates) == set(FEES)


def test_bounding_box_rejects_before_walking_the_ring():
    """The bbox is a fast reject, not a second opinion — a point inside the box
    but outside the shape must still be excluded."""
    # An L-shape: the notch is inside the bounding box but outside the polygon.
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]],
    }
    zone = Zone(
        id=None,  # type: ignore[arg-type]
        name="L",
        region_slug=None,
        delivery_fee=Decimal("35.00"),
        min_lat=0.0,
        max_lat=2.0,
        min_lng=0.0,
        max_lng=2.0,
        rings=(
            (tuple((float(c[0]), float(c[1])) for c in geometry["coordinates"][0]),),
        ),
    )
    assert zone.contains(0.5, 0.5) is True  # in the arm
    assert zone.contains(1.5, 1.5) is False  # in the notch, inside the bbox
    assert zone.contains(5.0, 5.0) is False  # outside entirely


def test_holes_are_cut_out_of_the_outline():
    """A ring after the first is a hole, not another shape."""
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ],
    }
    assert point_in_geometry(1, 1, geometry) is True  # inside outline
    assert point_in_geometry(5, 5, geometry) is False  # inside the hole
