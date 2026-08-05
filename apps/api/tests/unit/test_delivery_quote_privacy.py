"""
The courier is our business, not the customer's.

Two separate promises are kept here. The storefront quote must not hint at who
carries an order — not the courier's name, not its price, not the zone's
fulfilment setting. And the courier's estimate must still be captured, against
the basket, for every quote taken, so the fee table can be argued with using
real numbers later.

The reason this is a test and not a comment is that the leak would be one field
added to a response model, by someone who had no idea it mattered.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.api.v1.delivery import DeliveryQuoteResponse
from app.models.cart import Cart
from app.models.delivery_settings import DeliverySettings
from app.services import delivery_service, lalamove_service
from app.services.delivery_zone_service import Zone


@pytest.fixture(autouse=True)
def noon_send_is_off(monkeypatch):
    """
    Pin noon Send to unconfigured, whatever the developer's `.env` says.

    Every quote here goes through a mocked session, and a configured noon Send
    sends the `noon_send` zone down its own estimate path — which resolves a
    real pickup branch and dies on the mock with a `'coroutine' object has no
    attribute 'first'` that says nothing about what these tests are for.

    Left implicit, that made the file pass or fail depending on whether the
    machine running it happened to have a courier key on disk: green in CI,
    three red on the laptop of whoever had one. Pinned, so the answer is the
    same everywhere.
    """
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "NOON_SEND_API_KEY", "")


SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("150.00"),
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
)

SHARJAH_CITY = Zone(
    id=uuid.uuid4(),
    name="Sharjah City",
    delivery_fee=Decimal("15.00"),
    fulfilment_provider="lalamove",
    min_lat=25.0,
    max_lat=25.6,
    min_lng=55.2,
    max_lng=55.7,
    rings=(),
    free_delivery_eligible=True,
)

SHARJAH_CENTRAL = Zone(
    id=uuid.uuid4(),
    name="Sharjah Central",
    delivery_fee=Decimal("15.00"),
    fulfilment_provider="noon_send",
    min_lat=25.2,
    max_lat=25.5,
    min_lng=55.2,
    max_lng=55.5,
    rings=(),
)

ESTIMATE = lalamove_service.Estimate(
    cost=Decimal("25.00"), currency="AED", distance_m=4500, quotation_id="q_123"
)


@pytest.fixture
def cart() -> Cart:
    return Cart(id=uuid.uuid4(), session_id="sess_test")


def _patches(estimate, error, zone=SHARJAH_CITY):
    return (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=zone),
        ),
        # Patched on Lalamove rather than on the router above it, so a test that
        # wants a noon Send zone costed on noon Send's own card can say so by
        # patching that one instead — which is the whole point of the router.
        patch.object(
            delivery_service.lalamove_service,
            "estimate_for_point",
            new=AsyncMock(return_value=(estimate, error)),
        ),
        # The arrival estimate walks the zone's schedule. Nothing here is about
        # the clock, and an empty schedule is a legal state anyway.
        patch.object(
            delivery_service.batching_service,
            "active_windows",
            new=AsyncMock(return_value=[]),
        ),
    )


async def _quote(
    cart, estimate=ESTIMATE, error=None, subtotal="100.00", zone=SHARJAH_CITY
):
    settings_p, zone_p, est_p, windows_p = _patches(estimate, error, zone)
    with settings_p, zone_p, est_p, windows_p:
        return await delivery_service.quote(
            AsyncMock(),
            Decimal(subtotal),
            latitude=25.3304,
            longitude=55.3710,
            cart=cart,
            address="Al Qasimia, Sharjah",
        )


async def test_the_quote_says_nothing_about_the_courier(cart):
    result = await _quote(cart)

    assert result["delivery_fee"] == 15.0
    blob = repr(result).lower()
    for leak in (
        "lalamove",
        "noon",
        "rod",
        "courier",
        "provider",
        "third_party",
        "quotation",
    ):
        assert leak not in blob, f"the storefront quote mentions {leak!r}"


async def test_no_zone_name_names_the_carrier(cart):
    """
    `zone_name` is the one field on the response that is free text, and it is
    the obvious place for "Sharjah — noon Send" to be typed by someone drawing
    a new map. It reaches the browser, so it may only ever name a place.
    """
    for zone in (SHARJAH_CITY, SHARJAH_CENTRAL):
        result = await _quote(cart, zone=zone)
        name = (result["zone_name"] or "").lower()
        for leak in ("lalamove", "noon", "rod", "courier"):
            assert leak not in name, f"the zone name mentions {leak!r}"


def test_the_response_model_has_no_field_that_could_leak_one():
    """
    Even if the service dict grew a courier field, FastAPI serialises through
    this model — so the model is the actual boundary, and it is checked here by
    name rather than by inspection of the dict above.
    """
    fields = set(DeliveryQuoteResponse.model_fields)
    assert fields == {
        "delivery_fee",
        "base_fee",
        "free_delivery_applied",
        # "free delivery does not reach here" — a fact about the offer, with no
        # hint as to why, and in particular none about who would have carried it.
        "free_delivery_available",
        "free_threshold",
        "remaining_for_free",
        "zone_name",
        "in_known_zone",
        # "we cannot deliver here", with no hint as to who would have.
        "serviceable",
        # When it arrives. A date and an hour — never a driver, a run, or a
        # courier's name.
        "delivery_estimate",
    }


async def test_a_noon_send_zone_promises_an_hour_without_reading_a_schedule(cart):
    """
    A noon Send order is handed to a rider the moment it is packed, so an hour
    is a promise that can be kept — and nothing about it depends on the batch
    schedule, which is why the schedule is made to explode if it is consulted.

    Asserted because the code path is one `if` on `is_batched` sitting between
    two branches that would both look plausible if it went missing.
    """
    settings_p, zone_p, est_p, _ = _patches(ESTIMATE, None, SHARJAH_CENTRAL)

    async def never(*_args, **_kwargs):
        raise AssertionError("a noon Send zone must not wait for a batch window")

    with (
        settings_p,
        zone_p,
        est_p,
        patch.object(delivery_service.batching_service, "active_windows", new=never),
    ):
        result = await delivery_service.quote(
            AsyncMock(),
            Decimal("100.00"),
            latitude=25.3304,
            longitude=55.3710,
            cart=cart,
            address="Al Qasimia, Sharjah",
        )

    assert result["delivery_estimate"]["precision"] == "time"


async def test_an_area_we_do_not_dispatch_promises_only_a_day(cart):
    """
    Outside every drawn zone the order is collected on somebody else's
    schedule. Naming an hour there would be inventing a precision that belongs
    to a third party.
    """
    result = await _quote(cart, zone=None)

    assert result["delivery_estimate"]["precision"] == "day"


async def test_the_promised_time_still_names_nobody(cart):
    result = await _quote(cart, zone=SHARJAH_CENTRAL)
    blob = repr(result).lower()
    for leak in ("lalamove", "noon", "rod", "courier", "rider"):
        assert leak not in blob, f"the storefront quote mentions {leak!r}"


async def test_the_estimate_is_filed_against_the_basket(cart):
    await _quote(cart)

    assert cart.delivery_quote_cost == Decimal("25.00")
    assert cart.delivery_quote_fee == Decimal("15.00")
    assert cart.delivery_quote_currency == "AED"
    assert cart.delivery_quote_distance_m == 4500
    assert cart.delivery_quote_reference == "q_123"
    assert cart.delivery_quote_provider == "lalamove"
    assert cart.delivery_quote_zone == "Sharjah City"
    assert cart.delivery_quote_error is None


async def test_free_delivery_records_the_revenue_as_zero(cart):
    """
    Above the threshold we absorb the entire courier cost. The basket has to
    record a fee of zero, not the fee we would have charged, or the margin
    report would flatter every large order.
    """
    result = await _quote(cart, subtotal="250.00")

    assert result["free_delivery_applied"] is True
    assert result["delivery_fee"] == 0.0
    assert cart.delivery_quote_fee == Decimal("0.00")
    assert cart.delivery_quote_cost == Decimal("25.00")


async def test_the_threshold_does_not_move_with_the_zone(cart):
    """
    One threshold for the whole country. Whether free delivery *applies* does
    depend on the zone — it reaches the fixed-fee ones and no further — but the
    number itself never moves, because a threshold that varied by address would
    be the one place the map became visible to the customer.
    """
    far = Zone(
        id=uuid.uuid4(),
        name="Fujairah",
        delivery_fee=Decimal("80.00"),
        fulfilment_provider="third_party",
        min_lat=24.8,
        max_lat=25.7,
        min_lng=55.9,
        max_lng=56.4,
        rings=(),
    )
    near = await _quote(cart, subtotal="149.00")
    far_quote = await _quote(cart, subtotal="149.00", zone=far)

    assert near["free_threshold"] == far_quote["free_threshold"] == 150.0
    assert near["remaining_for_free"] == far_quote["remaining_for_free"] == 1.0


async def test_a_failed_estimate_is_recorded_rather_than_swallowed(cart):
    """
    "No quote" and "a quote of nothing" are very different findings, and an
    address the courier refuses is exactly the kind we want to know about.
    """
    result = await _quote(
        cart, estimate=None, error="Address is outside the service area"
    )

    assert result["delivery_fee"] == 15.0, "the customer is still priced normally"
    assert cart.delivery_quote_cost is None
    assert cart.delivery_quote_error == "Address is outside the service area"


async def test_a_noon_send_zone_is_costed_on_its_own_rate_card(cart, monkeypatch):
    """
    Each zone is costed against the courier that actually serves it. Quoting a
    noon Send zone at Lalamove's prices would make Sharjah Central look like it
    loses AED 10 an order when it makes AED 3.
    """
    from app.core.config import settings as app_settings
    from app.services import courier_service, noon_send_service

    monkeypatch.setattr(app_settings, "NOON_SEND_API_KEY", "test-key")

    # A fixed off-peak instant. `rate_card_cost` reads the clock when it is not
    # given one, and the surge adds a dirham for a quarter of every day — so
    # without this the expected 12.00 becomes 13.00 between 12:00-15:00 and
    # 19:00-22:00 Dubai, which is most of when anyone would be running it.
    off_peak = datetime(2026, 8, 4, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))

    async def rate_card(db, latitude, longitude, address=None, branch_id=None):
        return (
            lalamove_service.Estimate(
                cost=noon_send_service.rate_card_cost(4.4, at=off_peak),
                currency="AED",
                distance_m=4400,
                # There is no quotation to reference — nobody issued one.
                quotation_id=None,
            ),
            None,
        )

    monkeypatch.setattr(
        courier_service.noon_send_service, "estimate_for_point", rate_card
    )
    result = await _quote(cart, zone=SHARJAH_CENTRAL)

    assert result["delivery_fee"] == 15.0
    assert cart.delivery_quote_provider == "noon_send"
    assert cart.delivery_quote_cost == Decimal("12.00")
    assert cart.delivery_quote_reference is None


async def test_a_courier_outage_cannot_stop_someone_checking_out(cart):
    """
    With no estimate and no error — the shape when Lalamove is switched off
    entirely — the quote still prices the basket from the zone map.
    """
    result = await _quote(cart, estimate=None, error=None)

    assert result["delivery_fee"] == 15.0
    assert result["zone_name"] == "Sharjah City"
    assert cart.delivery_quote_at is None, "nothing to record, so nothing written"
