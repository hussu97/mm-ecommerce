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
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.delivery_zone_service import point_in_geometry

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
DATA = Path(__file__).resolve().parents[2] / "app" / "data"

#: The migration that publishes the map currently in force. Everything here is
#: read out of it rather than restated, so the test cannot agree with a fee
#: table nobody is using.
LIVE_MIGRATION = "085_cost_banded_map.py"

#: The flat price of everywhere we do not dispatch ourselves. Read by the
#: fixture as well, so the migration's own constant is the only definition.
OUTER_FEE = "80.00"
OUTER_THRESHOLD = "200.00"

LALAMOVE = "lalamove"
NOON_SEND = "noon_send"
THIRD_PARTY = "third_party"


def _geojson_path() -> Path:
    """
    Whichever snapshot the live migration reads.

    Not the generator's output. The two are different files on purpose — a
    migration reads a frozen `.vN.` copy so that redrawing the map cannot change
    what an old migration means — and a test that validated the generator's
    output while the migration seeded something else would be checking a map
    nobody runs.
    """
    source = (VERSIONS / LIVE_MIGRATION).read_text()
    match = re.search(r'"(uae_delivery_zones[a-z0-9.]*\.json)"', source)
    assert match, f"{LIVE_MIGRATION} names no geometry file"
    return DATA / match.group(1)


def _seeded_zones() -> list[tuple[str, str, str, str, bool]]:
    """The fee table straight out of the migration, so the two cannot drift."""
    namespace: dict[str, object] = {
        "OUTER_FEE": OUTER_FEE,
        "OUTER_THRESHOLD": OUTER_THRESHOLD,
    }
    source = (VERSIONS / LIVE_MIGRATION).read_text()
    start = source.index("ZONES: list[tuple[str, str, str, str, bool]] = [")
    # From the opening bracket of the literal, not from the annotation — which
    # has brackets of its own and would close the slice three characters in.
    end = source.index("\n]", source.index("= [", start)) + 2
    exec(source[start:end], namespace)  # noqa: S102 — our own file, no input
    return namespace["ZONES"]  # type: ignore[return-value]


@pytest.fixture(scope="module")
def zones() -> list[dict]:
    """Geometry in the order the migration inserts it, which is match order."""
    shapes = {z["name"]: z["geometry"] for z in json.loads(_geojson_path().read_text())}
    return [
        {
            "name": name,
            "fee": Decimal(fee),
            "threshold": Decimal(threshold),
            "provider": provider,
            "geometry": shapes[name],
        }
        for name, fee, threshold, provider, _free in _seeded_zones()
    ]


def resolve(zones: list[dict], lat: float, lng: float) -> dict | None:
    for zone in zones:
        if point_in_geometry(lat, lng, zone["geometry"]):
            return zone
    return None


@pytest.mark.parametrize(
    "label,lat,lng,expected,fee,threshold,provider",
    [
        # ── Sharjah, inside noon Send's 20 km reach: free, at any basket ─────
        # The fee is zero rather than waived, so the threshold is 0.00 and not
        # NULL. NULL would mean "use the national number"; zero means there is
        # no bar to clear, and confusing the two starts charging the one zone
        # that is meant to be free.
        (
            "Melting Moments itself",
            25.3304139,
            55.3736131,
            "Sharjah Central",
            "0.00",
            "0.00",
            NOON_SEND,
        ),
        (
            "Al Majaz Waterfront",
            25.3213,
            55.3820,
            "Sharjah Central",
            "0.00",
            "0.00",
            NOON_SEND,
        ),
        ("Al Khan", 25.3306, 55.3600, "Sharjah Central", "0.00", "0.00", NOON_SEND),
        ("Maysaloon", 25.3220, 55.4250, "Sharjah Central", "0.00", "0.00", NOON_SEND),
        (
            "Muwaileh Commercial",
            25.3120,
            55.4560,
            "Sharjah Central",
            "0.00",
            "0.00",
            NOON_SEND,
        ),
        ("Al Zahia", 25.3000, 55.4700, "Sharjah Central", "0.00", "0.00", NOON_SEND),
        (
            "University City",
            25.2900,
            55.4900,
            "Sharjah Central",
            "0.00",
            "0.00",
            NOON_SEND,
        ),
        # Al Taawun belongs here too and does not resolve — see
        # `test_al_taawun_is_in_a_hole_in_the_source_outlines` below.
        # ── Sharjah, past noon Send's ceiling ────────────────────────────────
        # 21.1 road km. noon Send would be cheaper here too; it is not allowed
        # to be, so the boundary sits at the ceiling and not where prices cross.
        # Priced as Dubai: a car run at AED 32 of cost.
        ("Al Rahmaniya", 25.2760, 55.5200, "Sharjah Outer", "20.00", "75.00", LALAMOVE),
        # ── Another emirate, so never noon Send whatever the distance ────────
        ("Ajman Corniche", 25.4052, 55.4384, "Ajman City", "10.00", "75.00", LALAMOVE),
        (
            "Emirates City, Ajman",
            25.4180,
            55.5140,
            "Ajman City",
            "10.00",
            "75.00",
            LALAMOVE,
        ),
        # ── Dubai: three bands, one fee, so the far half can be repriced later
        #    without redrawing anything ─────────────────────────────────────
        (
            "Deira City Centre",
            25.2530,
            55.3320,
            "Dubai Near",
            "20.00",
            "75.00",
            LALAMOVE,
        ),
        ("Burj Khalifa", 25.1972, 55.2744, "Dubai Near", "20.00", "75.00", LALAMOVE),
        (
            "Dubai Silicon Oasis",
            25.1200,
            55.3800,
            "Dubai Mid",
            "20.00",
            "75.00",
            LALAMOVE,
        ),
        ("Dubai Marina", 25.0805, 55.1403, "Dubai Far", "20.00", "75.00", LALAMOVE),
        ("Palm Jumeirah", 25.1304, 55.1170, "Dubai Far", "20.00", "75.00", LALAMOVE),
        # Jebel Ali used to fall outside the served circle and pay the
        # third-party 80. A Lalamove car reaches it for 56, so it is inside now.
        ("Jebel Ali", 24.9500, 55.1500, "Dubai Far", "20.00", "75.00", LALAMOVE),
        # ── Emirates that now have a served band of their own ────────────────
        # A car costs 44 here against the third-party 80, so it is worth serving.
        (
            "Umm Al Quwain city",
            25.5647,
            55.5532,
            "Umm al-Quwain City",
            "30.00",
            "75.00",
            LALAMOVE,
        ),
        # 89 road km. A car costs 80 — level with the third party — and the
        # threshold is 100 rather than 75 because the run is three times a
        # Dubai one.
        (
            "Ras Al Khaimah city",
            25.7895,
            55.9432,
            "Ras al-Khaimah City",
            "50.00",
            "100.00",
            LALAMOVE,
        ),
        # ── Ours to deliver, but not on a courier's price ────────────────────
        # All of these look like "Sharjah" or "Dubai" on an address form and
        # cost three to six times a city run. This is the whole reason the
        # emirate outlines were cut up.
        (
            "Al Dhaid (inland Sharjah)",
            25.2880,
            55.8810,
            "Sharjah",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Khor Fakkan (east coast)",
            25.3390,
            56.3560,
            "Sharjah",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Kalba (east coast)",
            25.0400,
            56.3500,
            "Sharjah",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Masfout (Ajman exclave)",
            24.8200,
            56.0500,
            "Ajman",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Manama (Ajman exclave)",
            25.3100,
            55.9800,
            "Ajman",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        # Lalamove refuses Hatta outright — ERR_OUT_OF_SERVICE_AREA.
        (
            "Hatta (Dubai exclave)",
            24.7967,
            56.1180,
            "Dubai",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Sheikh Zayed Grand Mosque",
            24.4128,
            54.4750,
            "Abu Dhabi",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Al Ain Oasis",
            24.2154,
            55.7614,
            "Abu Dhabi",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
        (
            "Fujairah city",
            25.1288,
            56.3265,
            "Fujairah",
            OUTER_FEE,
            OUTER_THRESHOLD,
            THIRD_PARTY,
        ),
    ],
)
def test_real_addresses_get_the_right_zone(
    zones, label, lat, lng, expected, fee, threshold, provider
):
    zone = resolve(zones, lat, lng)
    assert zone is not None, f"{label} matched no zone at all"
    assert zone["name"] == expected, label
    assert zone["fee"] == Decimal(fee), label
    assert zone["threshold"] == Decimal(threshold), label
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
        ("Sharjah Central", "Sharjah Outer"),
        ("Sharjah Outer", "Sharjah"),
        ("Ajman City", "Ajman"),
        ("Dubai Near", "Dubai Mid"),
        ("Dubai Mid", "Dubai Far"),
        ("Dubai Far", "Dubai"),
        ("Umm al-Quwain City", "Umm al-Quwain"),
        ("Ras al-Khaimah City", "Ras al-Khaimah"),
    ):
        assert order.index(inner) < order.index(outer)


def test_al_taawun_is_served_even_though_no_emirate_claims_it(zones):
    """
    Al Taawun sits in Khalid Lagoon, 0.13 km outside the Sharjah outline and
    1.6 km from the kitchen. No emirate outline claims the water, so under
    `emirate ∩ circle` the address belonged to no zone at all and was quoted
    live by a courier instead of being delivered free like its neighbours.

    The bands now close gaps in their own emirate's favour, bounded to
    `GAP_FILL_KM`. That bound is the point: an unbounded version claimed open
    sea on the Dubai side, where noon Send would have been offered an order it
    cannot legally carry and the fallback would have run a Lalamove car for the
    zone's fee of zero.
    """
    zone = resolve(zones, 25.3160, 55.3720)
    assert zone is not None, "Al Taawun matched no zone"
    assert zone["name"] == "Sharjah Central"
    assert zone["fee"] == Decimal("0.00")


def test_the_gap_fill_does_not_reach_open_water_on_the_dubai_side(zones):
    """
    The regression that killed the first attempt, pinned by coordinate.

    25.254, 55.272 is sea 10.8 km from the Sharjah outline and 0.17 km from
    Dubai's. It must never be Sharjah's, because Sharjah's inner band is
    noon Send's and noon Send cannot cross an emirate boundary — an order there
    would be refused, fall back to Lalamove, and be charged nothing.
    """
    zone = resolve(zones, 25.25404020255122, 55.272435077642456)
    assert zone is None or zone["provider"] != NOON_SEND


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
                    # Inside Sharjah, or on ground no emirate claims at all.
                    # The second case is deliberate — the bands close gaps in
                    # the source outlines, which is how Al Taawun gets served —
                    # but it must never be ground another emirate *does* claim,
                    # because noon Send cannot cross a boundary and the fallback
                    # would run a Lalamove car for this zone's fee of zero.
                    trespass = [
                        name
                        for name, outline in outlines.items()
                        if name != "Sharjah" and point_in_geometry(lat, lng, outline)
                    ]
                    assert not trespass, (
                        f"{zone['name']} reaches into {trespass} at {lat}, {lng}"
                    )
                lng += 0.004
            lat += 0.004

    # A grid that found nothing inside would pass vacuously.
    assert checked > 100


def test_the_map_covers_every_seeded_zone(zones):
    """A fee with no shape would be a zone that can never match."""
    shapes = {z["name"] for z in json.loads(_geojson_path().read_text())}
    assert {z["name"] for z in zones} <= shapes
