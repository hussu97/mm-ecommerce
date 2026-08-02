"""
The Lalamove zone map has to charge the right fee at real addresses.

Every case below is a place with coordinates anyone can check, run against the
same GeoJSON the migration seeds and the same fee table it writes. The point of
the exercise was to stop paying AED 130 to deliver a AED 60 cake, so the tests
that matter are the boundary ones: the parts of Sharjah and Dubai that look
local on a map and are not.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.delivery_zone_service import point_in_geometry

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
GEOJSON = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.geojson.json"
)

LALAMOVE = "lalamove"
THIRD_PARTY = "third_party"


def _seeded_zones() -> dict[str, tuple[str, str, str]]:
    """The fee table straight out of the migration, so the two cannot drift."""
    namespace: dict[str, object] = {}
    source = (VERSIONS / "050_delivery_zone_fulfilment.py").read_text()
    start = source.index("ZONES: dict[str, tuple[str, str, str]] = {")
    end = source.index("}", start) + 1
    exec(source[start:end], namespace)  # noqa: S102 — our own file, no input
    return namespace["ZONES"]  # type: ignore[return-value]


@pytest.fixture(scope="module")
def zones() -> list[dict]:
    """Geometry in the order the migration inserts it, which is match order."""
    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON.read_text())}
    return [
        {
            "name": name,
            "fee": Decimal(fee),
            "provider": provider,
            "geometry": shapes[name],
        }
        for name, (fee, _slug, provider) in _seeded_zones().items()
    ]


def resolve(zones: list[dict], lat: float, lng: float) -> dict | None:
    for zone in zones:
        if point_in_geometry(lat, lng, zone["geometry"]):
            return zone
    return None


@pytest.mark.parametrize(
    "label,lat,lng,expected,fee,provider",
    [
        # ── Served by the courier ────────────────────────────────────────────
        (
            "Melting Moments itself",
            25.3304139,
            55.3710382,
            "Sharjah City",
            "15.00",
            LALAMOVE,
        ),
        ("Al Majaz Waterfront", 25.3213, 55.3820, "Sharjah City", "15.00", LALAMOVE),
        ("University City", 25.2900, 55.4900, "Sharjah City", "15.00", LALAMOVE),
        ("Muwaileh Commercial", 25.3120, 55.4560, "Sharjah City", "15.00", LALAMOVE),
        ("Ajman Corniche", 25.4052, 55.4384, "Ajman City", "15.00", LALAMOVE),
        ("Emirates City, Ajman", 25.4180, 55.5140, "Ajman City", "15.00", LALAMOVE),
        ("Deira City Centre", 25.2530, 55.3320, "Dubai City", "25.00", LALAMOVE),
        ("Burj Khalifa", 25.1972, 55.2744, "Dubai City", "25.00", LALAMOVE),
        ("Dubai Silicon Oasis", 25.1200, 55.3800, "Dubai City", "25.00", LALAMOVE),
        ("Dubai Marina", 25.0805, 55.1403, "Dubai City", "25.00", LALAMOVE),
        # The furthest point the rate card was measured at: 48 road km, AED 51
        # of courier cost against a AED 25 fee. Inside on purpose — Dubai is a
        # third of expected demand and the loss is carried by the near half.
        ("Palm Jumeirah", 25.1304, 55.1170, "Dubai City", "25.00", LALAMOVE),
        # ── Ours to deliver, but not on the courier's price ──────────────────
        # All of these look like "Sharjah" or "Dubai" on an address form and
        # cost three to six times a city run. This is the whole reason the
        # emirate outlines were cut up.
        (
            "Al Dhaid (inland Sharjah)",
            25.2880,
            55.8810,
            "Sharjah",
            "50.00",
            THIRD_PARTY,
        ),
        ("Khor Fakkan (east coast)", 25.3390, 56.3560, "Sharjah", "50.00", THIRD_PARTY),
        ("Kalba (east coast)", 25.0400, 56.3500, "Sharjah", "50.00", THIRD_PARTY),
        ("Masfout (Ajman exclave)", 24.8200, 56.0500, "Ajman", "50.00", THIRD_PARTY),
        ("Manama (Ajman exclave)", 25.3100, 55.9800, "Ajman", "50.00", THIRD_PARTY),
        ("Jebel Ali", 24.9500, 55.1500, "Dubai", "50.00", THIRD_PARTY),
        # Lalamove refuses Hatta outright — ERR_OUT_OF_SERVICE_AREA.
        ("Hatta (Dubai exclave)", 24.7967, 56.1180, "Dubai", "50.00", THIRD_PARTY),
        (
            "Sheikh Zayed Grand Mosque",
            24.4128,
            54.4750,
            "Abu Dhabi",
            "50.00",
            THIRD_PARTY,
        ),
        ("Al Ain Oasis", 24.2154, 55.7614, "Abu Dhabi", "50.00", THIRD_PARTY),
        (
            "Ras Al Khaimah city",
            25.7895,
            55.9432,
            "Ras al-Khaimah",
            "50.00",
            THIRD_PARTY,
        ),
        ("Fujairah city", 25.1288, 56.3265, "Fujairah", "50.00", THIRD_PARTY),
        ("Umm Al Quwain city", 25.5647, 55.5532, "Umm al-Quwain", "50.00", THIRD_PARTY),
    ],
)
def test_real_addresses_get_the_right_zone(
    zones, label, lat, lng, expected, fee, provider
):
    zone = resolve(zones, lat, lng)
    assert zone is not None, f"{label} matched no zone at all"
    assert zone["name"] == expected, label
    assert zone["fee"] == Decimal(fee), label
    assert zone["provider"] == provider, label


def test_a_city_is_listed_ahead_of_its_own_emirate(zones):
    """
    Sharjah City sits inside Sharjah, so both contain the shop. The fee that
    applies is whichever is tested first — if that order inverted, every local
    delivery would silently jump from 15 to 50.
    """
    order = [z["name"] for z in zones]
    for city, emirate in (
        ("Sharjah City", "Sharjah"),
        ("Ajman City", "Ajman"),
        ("Dubai City", "Dubai"),
    ):
        assert order.index(city) < order.index(emirate)


def test_outside_the_country_matches_nothing(zones):
    """Muscat falls through to the configured default rather than a border zone."""
    assert resolve(zones, 23.5880, 58.3829) is None


def test_every_courier_zone_is_cheaper_than_the_third_party_fee(zones):
    """
    A courier zone exists to charge less than the manual one. If a redraw ever
    left one at 50 it would be doing nothing but adding an integration.
    """
    for zone in zones:
        if zone["provider"] == LALAMOVE:
            assert zone["fee"] < Decimal("50.00"), zone["name"]


def test_every_zone_names_a_known_courier(zones):
    assert {z["provider"] for z in zones} <= {LALAMOVE, THIRD_PARTY}


def test_the_map_covers_every_seeded_zone(zones):
    """A fee with no shape would be a zone that can never match."""
    shapes = {z["name"] for z in json.loads(GEOJSON.read_text())}
    assert {z["name"] for z in zones} <= shapes
