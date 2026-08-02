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
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.delivery import DeliveryQuoteResponse
from app.models.cart import Cart
from app.models.delivery_settings import DeliverySettings
from app.services import delivery_service, lalamove_service
from app.services.delivery_zone_service import Zone

SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("200.00"),
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
        patch.object(
            delivery_service.lalamove_service,
            "estimate_for_point",
            new=AsyncMock(return_value=(estimate, error)),
        ),
    )


async def _quote(
    cart, estimate=ESTIMATE, error=None, subtotal="100.00", zone=SHARJAH_CITY
):
    settings_p, zone_p, est_p = _patches(estimate, error, zone)
    with settings_p, zone_p, est_p:
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
    for leak in ("lalamove", "courier", "provider", "third_party", "quotation"):
        assert leak not in blob, f"the storefront quote mentions {leak!r}"


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
        "free_threshold",
        "remaining_for_free",
        "zone_name",
        "in_known_zone",
    }


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
    Free delivery is the same promise everywhere, including the zones no
    courier API touches. A threshold that varied by address would be the one
    place the map became visible to the customer.
    """
    far = Zone(
        id=uuid.uuid4(),
        name="Fujairah",
        delivery_fee=Decimal("50.00"),
        fulfilment_provider="third_party",
        min_lat=24.8,
        max_lat=25.7,
        min_lng=55.9,
        max_lng=56.4,
        rings=(),
    )
    near = await _quote(cart, subtotal="199.00")
    far_quote = await _quote(cart, subtotal="199.00", zone=far)

    assert near["free_threshold"] == far_quote["free_threshold"] == 200.0
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


async def test_a_courier_outage_cannot_stop_someone_checking_out(cart):
    """
    With no estimate and no error — the shape when Lalamove is switched off
    entirely — the quote still prices the basket from the zone map.
    """
    result = await _quote(cart, estimate=None, error=None)

    assert result["delivery_fee"] == 15.0
    assert result["zone_name"] == "Sharjah City"
    assert cart.delivery_quote_at is None, "nothing to record, so nothing written"
