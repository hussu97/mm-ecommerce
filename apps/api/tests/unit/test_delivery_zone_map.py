"""
The courier zone map has to charge the right fee at real addresses.

Every case below is a place with coordinates anyone can check, run against the
same GeoJSON the migration seeds and the same fee table it writes. The point of
the exercise was to stop paying AED 130 to deliver a AED 60 cake, so the tests
that matter are the boundary ones: the parts of Sharjah and Dubai that look
local on a map and are not — and now the line inside Sharjah where one courier
stops being able to reach and the other takes over.
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

#: The migration that publishes the map currently in force. Everything here is
#: read out of it rather than restated, so the test cannot agree with a fee
#: table nobody is using.
LIVE_MIGRATION = "065_sharjah_central_noon_send.py"

#: The flat price of everywhere we do not dispatch ourselves. Read by the
#: fixture as well, so the migration's own constant is the only definition.
OUTER_FEE = "80.00"

LALAMOVE = "lalamove"
NOON_SEND = "noon_send"
THIRD_PARTY = "third_party"


def _seeded_zones() -> list[tuple[str, str, str, bool]]:
    """The fee table straight out of the migration, so the two cannot drift."""
    namespace: dict[str, object] = {"OUTER_FEE": OUTER_FEE}
    source = (VERSIONS / LIVE_MIGRATION).read_text()
    start = source.index("ZONES: list[tuple[str, str, str, bool]] = [")
    # From the opening bracket of the literal, not from the annotation — which
    # has brackets of its own and would close the slice three characters in.
    end = source.index("\n]", source.index("= [", start)) + 2
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
        for name, fee, provider, _free in _seeded_zones()
    ]


def resolve(zones: list[dict], lat: float, lng: float) -> dict | None:
    for zone in zones:
        if point_in_geometry(lat, lng, zone["geometry"]):
            return zone
    return None


@pytest.mark.parametrize(
    "label,lat,lng,expected,fee,provider",
    [
        # ── Inside noon Send's 20 km reach ───────────────────────────────────
        # Same AED 15 the customer has always paid; the run behind it costs 12
        # instead of the 19-26 Lalamove wants for the same trip.
        (
            "Melting Moments itself",
            25.3304139,
            55.3736131,
            "Sharjah Central",
            "15.00",
            NOON_SEND,
        ),
        (
            "Al Majaz Waterfront",
            25.3213,
            55.3820,
            "Sharjah Central",
            "15.00",
            NOON_SEND,
        ),
        ("Al Khan", 25.3306, 55.3600, "Sharjah Central", "15.00", NOON_SEND),
        ("Maysaloon", 25.3220, 55.4250, "Sharjah Central", "15.00", NOON_SEND),
        # 12.8 road km, comfortably inside.
        (
            "Muwaileh Commercial",
            25.3120,
            55.4560,
            "Sharjah Central",
            "15.00",
            NOON_SEND,
        ),
        # 15.3 and 18.7 road km — inside the 20 km ceiling, and the two areas
        # the corrected rate card moved across the line.
        ("Al Zahia", 25.3000, 55.4700, "Sharjah Central", "15.00", NOON_SEND),
        (
            "University City",
            25.2900,
            55.4900,
            "Sharjah Central",
            "15.00",
            NOON_SEND,
        ),
        # ── Sharjah, but past noon Send's ceiling ────────────────────────────
        # 23.7 road km. noon Send would be cheaper here too — it is not allowed
        # to be, which is why this boundary sits at the ceiling rather than
        # where the prices cross.
        ("Al Rahmaniya", 25.2760, 55.5200, "Sharjah City", "15.00", LALAMOVE),
        # ── Another emirate, so never noon Send whatever the distance ────────
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
            OUTER_FEE,
            THIRD_PARTY,
        ),
        (
            "Khor Fakkan (east coast)",
            25.3390,
            56.3560,
            "Sharjah",
            OUTER_FEE,
            THIRD_PARTY,
        ),
        ("Kalba (east coast)", 25.0400, 56.3500, "Sharjah", OUTER_FEE, THIRD_PARTY),
        ("Masfout (Ajman exclave)", 24.8200, 56.0500, "Ajman", OUTER_FEE, THIRD_PARTY),
        ("Manama (Ajman exclave)", 25.3100, 55.9800, "Ajman", OUTER_FEE, THIRD_PARTY),
        ("Jebel Ali", 24.9500, 55.1500, "Dubai", OUTER_FEE, THIRD_PARTY),
        # Lalamove refuses Hatta outright — ERR_OUT_OF_SERVICE_AREA.
        ("Hatta (Dubai exclave)", 24.7967, 56.1180, "Dubai", OUTER_FEE, THIRD_PARTY),
        (
            "Sheikh Zayed Grand Mosque",
            24.4128,
            54.4750,
            "Abu Dhabi",
            OUTER_FEE,
            THIRD_PARTY,
        ),
        ("Al Ain Oasis", 24.2154, 55.7614, "Abu Dhabi", OUTER_FEE, THIRD_PARTY),
        (
            "Ras Al Khaimah city",
            25.7895,
            55.9432,
            "Ras al-Khaimah",
            OUTER_FEE,
            THIRD_PARTY,
        ),
        ("Fujairah city", 25.1288, 56.3265, "Fujairah", OUTER_FEE, THIRD_PARTY),
        (
            "Umm Al Quwain city",
            25.5647,
            55.5532,
            "Umm al-Quwain",
            OUTER_FEE,
            THIRD_PARTY,
        ),
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


@pytest.mark.parametrize(
    "label,lat,lng",
    [
        ("the shop itself", 25.3304139, 55.3736131),
        ("Deira, on the Dubai city edge", 25.2530, 55.3320),
        ("Al Nahda, on the Dubai–Sharjah line", 25.2980, 55.3760),
        ("Ajman Corniche", 25.4052, 55.4384),
        ("Jebel Ali, just outside the served circle", 24.9500, 55.1500),
        ("Al Dhaid, inland Sharjah", 25.2880, 55.8810),
    ],
)
def test_exactly_one_zone_claims_each_point(zones, label, lat, lng):
    """
    A served city is punched out of its emirate rather than laid on top of it,
    so no address is inside two zones at once.

    The alternative — overlap plus `display_order` — prices correctly only for
    as long as nobody reorders the rows, and gives a map you cannot read: two
    translucent fills over Deira with no way to tell which fee applies there.
    """
    matches = [z["name"] for z in zones if point_in_geometry(lat, lng, z["geometry"])]
    assert len(matches) == 1, f"{label} matched {matches}"


def test_a_city_is_still_listed_ahead_of_its_own_emirate(zones):
    """
    Belt and braces. The shapes no longer overlap, so order does not decide the
    fee any more — but the smaller, more specific zone being tested first is
    the cheaper lookup and the convention the map is drawn in, and a redraw
    that reintroduced an overlap would price correctly rather than jumping
    every local delivery from 15 to 50.
    """
    order = [z["name"] for z in zones]
    for inner, outer in (
        ("Sharjah Central", "Sharjah City"),
        ("Sharjah City", "Sharjah"),
        ("Ajman City", "Ajman"),
        ("Dubai City", "Dubai"),
    ):
        assert order.index(inner) < order.index(outer)


def test_outside_the_country_matches_nothing(zones):
    """Muscat falls through to the configured default rather than a border zone."""
    assert resolve(zones, 23.5880, 58.3829) is None


def test_every_courier_zone_is_cheaper_than_the_third_party_fee(zones):
    """
    A courier zone exists to charge less than the manual one. If a redraw ever
    left one at the outer fee it would be doing nothing but adding an
    integration.
    """
    for zone in zones:
        if zone["provider"] != THIRD_PARTY:
            assert zone["fee"] < Decimal(OUTER_FEE), zone["name"]


def test_every_zone_names_a_known_courier(zones):
    assert {z["provider"] for z in zones} <= {LALAMOVE, NOON_SEND, THIRD_PARTY}


def test_noon_send_only_ever_claims_sharjah(zones):
    """
    noon Send cannot cross an emirate boundary, and the kitchen is in Sharjah.
    A zone naming them anywhere else is a map that books tasks certain to be
    refused, so this is checked against the emirate outlines rather than against
    the zone's name.

    Sampled on a grid rather than at the zone's own vertices: a clipped ring
    borrows its corners from the emirate outline, and a point sitting exactly on
    the Sharjah-Ajman line belongs to whichever of the two the ray-casting
    happens to favour. Grid points do not land on borders.
    """
    outlines = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "app"
            / "data"
            / "uae_emirates.geojson.json"
        ).read_text()
    )
    noon_send_zones = [z for z in zones if z["provider"] == NOON_SEND]
    assert noon_send_zones, "no zone is served by noon Send"

    checked = 0
    for zone in noon_send_zones:
        lats = [c[1] for poly in zone["geometry"]["coordinates"] for c in poly[0]]
        lngs = [c[0] for poly in zone["geometry"]["coordinates"] for c in poly[0]]
        lat = min(lats)
        while lat <= max(lats):
            lng = min(lngs)
            while lng <= max(lngs):
                if point_in_geometry(lat, lng, zone["geometry"]):
                    checked += 1
                    assert point_in_geometry(lat, lng, outlines["Sharjah"]), (
                        f"{zone['name']} reaches outside Sharjah at {lat}, {lng}"
                    )
                lng += 0.004
            lat += 0.004

    # A grid that found nothing inside would pass vacuously.
    assert checked > 100


def test_the_map_covers_every_seeded_zone(zones):
    """A fee with no shape would be a zone that can never match."""
    shapes = {z["name"] for z in json.loads(GEOJSON.read_text())}
    assert {z["name"] for z in zones} <= shapes
