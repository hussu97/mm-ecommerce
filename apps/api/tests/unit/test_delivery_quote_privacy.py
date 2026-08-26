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
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.api.v1.delivery import DeliveryQuoteResponse
from app.models.cart import Cart
from app.models.delivery_settings import DeliverySettings
from app.services.couriers import lalamove_service
from app.services.delivery import delivery_promise, delivery_service
from app.services.delivery.delivery_zone_service import Zone


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
    pickup_fee=Decimal("0.00"),
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
    free_delivery_threshold=Decimal("150.00"),
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

#: A fixed arrival, so a test asserting the quote's *shape* does not also depend
#: on the resolver's four rules — those have their own module.
PROMISED = delivery_promise.DeliveryPromise(
    at=datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc),
    precision="time",
    reason="stubbed for this module",
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
        # The arrival estimate is its own resolver now, and it reads a courier
        # row, a group, a schedule and the branch's trading hours. None of that
        # is what this module is about — these tests are about what the quote
        # says and does not say about the carrier — so the whole resolver is
        # stubbed rather than each of its four lookups.
        patch.object(
            delivery_service.delivery_promise,
            "promise_for_zone",
            new=AsyncMock(return_value=PROMISED),
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
        # The threshold that applied to this pin. Zones set their own, so this
        # varies by address — but it is a number about a basket, and knowing
        # that Sharjah goes free at 40 and Abu Dhabi at 200 says nothing about
        # who carries either one.
        "free_threshold",
        # Whether the number above is still the national default because no pin
        # has been dropped yet. A statement about our own certainty.
        "free_threshold_provisional",
        "remaining_for_free",
        "zone_name",
        "in_known_zone",
        # "we cannot deliver here", with no hint as to who would have.
        "serviceable",
        # When it arrives. A date and an hour — never a driver, a run, or a
        # courier's name.
        "delivery_estimate",
    }


async def test_the_quote_asks_the_resolver_rather_than_deciding_itself(cart):
    """
    Every delivery time in the app comes from `delivery_promise`, so that the
    card, the checkout, the confirmation and the email cannot disagree. The
    quote's job is to pass the zone in and render what comes back.

    Asserted by making the resolver the only way to get an answer: if `quote`
    ever grows its own arithmetic again, this stops raising and starts lying.
    """
    settings_p, zone_p, est_p, _ = _patches(ESTIMATE, None, SHARJAH_CENTRAL)

    async def never(*_args, **_kwargs):
        raise AssertionError("the quote computed a delivery time of its own")

    with (
        settings_p,
        zone_p,
        est_p,
        patch.object(delivery_service.delivery_promise, "promise_for_zone", new=never),
        pytest.raises(AssertionError, match="computed a delivery time"),
    ):
        await delivery_service.quote(
            AsyncMock(),
            Decimal("100.00"),
            latitude=25.3304,
            longitude=55.3710,
            cart=cart,
            address="Al Qasimia, Sharjah",
        )


async def test_a_pin_outside_every_zone_is_refused_rather_than_quoted(cart):
    """
    It used to be priced at a national default and promised "some day". The map
    tiles the country, so matching nothing means outside it — and a quote for an
    address we cannot serve is worse than a refusal the checkout can act on.
    """
    result = await _quote(cart, zone=None)

    assert result["serviceable"] is False
    assert result["delivery_fee"] is None


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


async def test_the_threshold_moves_with_the_zone(cart):
    """
    The inversion. There used to be one national threshold on the grounds that a
    number varying by address would make the map visible to the customer — but
    one number is simultaneously too high for a bike run inside Sharjah at AED 13
    and too low for a car to Jebel Ali at AED 59, so it was withholding a cheap
    offer and funding an expensive one at the same time.
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
        free_delivery_threshold=Decimal("200.00"),
    )
    near = await _quote(cart, subtotal="149.00")
    far_quote = await _quote(cart, subtotal="149.00", zone=far)

    assert near["free_threshold"] == 150.0
    assert far_quote["free_threshold"] == 200.0
    # And the countdown follows it: one dirham to go in Sharjah, fifty-one out
    # at the third-party edge, off the same basket.
    assert near["remaining_for_free"] == 1.0
    assert far_quote["remaining_for_free"] == 51.0


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
    from app.services.couriers import courier_service, noon_send_service

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


async def test_a_non_pilot_slider_zone_parks_its_fallback_courier(cart, monkeypatch):
    """
    The provider filed against the basket is the one the quote was costed
    against, not the zone's raw `slider`. A non-pilot customer in a Slider zone
    is quoted — and later dispatched — on the fallback, so parking `slider` here
    would disagree with the courier the estimate actually belongs to and would
    reintroduce the Slider name onto an order that never touches Slider.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SLIDER_API_KEY", "sk_test")
    monkeypatch.setattr(app_settings, "SLIDER_TRIAL_EMAILS", "pilot@example.com")

    ajman = Zone(
        id=uuid.uuid4(),
        name="Ajman City",
        delivery_fee=Decimal("10.00"),
        fulfilment_provider="slider",
        min_lat=25.3,
        max_lat=25.5,
        min_lng=55.4,
        max_lng=55.6,
        rings=(),
        free_delivery_eligible=True,
        free_delivery_threshold=Decimal("75.00"),
    )

    settings_p, zone_p, est_p, windows_p = _patches(ESTIMATE, None, ajman)
    with settings_p, zone_p, est_p, windows_p:
        await delivery_service.quote(
            AsyncMock(),
            Decimal("100.00"),
            latitude=25.4052,
            longitude=55.4384,
            cart=cart,
            address="Ajman",
            user_id=uuid.uuid4(),
            email="someone@else.com",
        )

    assert cart.delivery_quote_provider == "lalamove"
    assert cart.delivery_quote_zone == "Ajman City"


async def test_a_courier_outage_cannot_stop_someone_checking_out(cart):
    """
    With no estimate and no error — the shape when Lalamove is switched off
    entirely — the quote still prices the basket from the zone map.
    """
    result = await _quote(cart, estimate=None, error=None)

    assert result["delivery_fee"] == 15.0
    # The emirate, not the operational band. "Sharjah City" is freight geography
    # — the cost bands exist so the courier-margin report can tell a bike run
    # inside the city from a car to the outskirts — and a customer reading it on
    # a checkout learns nothing except to wonder which part they are in.
    assert result["zone_name"] == "Sharjah"
    assert cart.delivery_quote_at is None, "nothing to record, so nothing written"
