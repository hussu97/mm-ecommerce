"""
Two ways of pricing one country.

Near the kitchen a zone publishes a fee and absorbs whatever a run costs.
Everywhere else the customer is quoted the courier's own price for their exact
pin. The whole point of the second mode is that it has a failure the first one
does not: a pin nobody will quote has no price, and there is nothing honest to
charge for it. These tests pin down which mode does what, and — more
importantly — that the failure is a refusal rather than a guess.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import delivery_service, lalamove_service
from app.services.delivery_zone_service import Zone
from app.services.providers.lalamove_provider import LalamoveError

SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("200.00"),
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
)


def _zone(fee: str, pricing_mode: str) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="Zone",
        delivery_fee=Decimal(fee),
        fulfilment_provider="lalamove",
        min_lat=24.0,
        max_lat=26.0,
        min_lng=54.0,
        max_lng=57.0,
        rings=(),
        pricing_mode=pricing_mode,
    )


def _estimate(cost: str) -> lalamove_service.Estimate:
    return lalamove_service.Estimate(
        cost=Decimal(cost), currency="AED", distance_m=12000, quotation_id="q"
    )


async def _price(
    *,
    zone: Zone | None,
    estimate: lalamove_service.Estimate | None,
    error: str | None = None,
    subtotal: str = "100.00",
    enabled: bool = True,
    latitude: float | None = 25.1,
    longitude: float | None = 55.2,
):
    with (
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
        patch.object(
            delivery_service.lalamove_service, "is_enabled", return_value=enabled
        ),
    ):
        return await delivery_service.price(
            AsyncMock(),
            Decimal(subtotal),
            latitude=latitude,
            longitude=longitude,
            address="Somewhere",
        )


# ── rounding ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "quoted,charged",
    [
        ("26.40", "27.00"),
        ("31.05", "32.00"),
        ("28.00", "28.00"),
        ("0.01", "1.00"),
        ("44.99", "45.00"),
    ],
)
def test_a_courier_price_is_rounded_up_to_the_dirham(quoted, charged):
    """
    Always up. Rounding to nearest would have us absorb the difference on
    roughly half of every dynamic order, for no gain anyone can see.
    """
    assert delivery_service.round_up_aed(Decimal(quoted)) == Decimal(charged)


def test_a_rounded_fee_has_no_fils_left_in_it():
    """It is rendered next to prices that never carry decimals."""
    assert str(delivery_service.round_up_aed(Decimal("26.40"))) == "27.00"


# ── which mode prices what ────────────────────────────────────────────────────


async def test_a_fixed_fee_zone_charges_its_published_fee():
    priced = await _price(zone=_zone("15.00", "static"), estimate=_estimate("41.20"))

    assert priced.base_fee == Decimal("15.00")
    assert priced.is_dynamic is False
    assert priced.serviceable is True


async def test_a_fixed_fee_zone_still_records_what_the_run_would_cost():
    """
    The gap between 15 charged and 41 paid is the entire argument for changing
    the number later, so it is collected even though nothing shows it.
    """
    priced = await _price(zone=_zone("15.00", "static"), estimate=_estimate("41.20"))

    assert priced.estimate is not None
    assert priced.estimate.cost == Decimal("41.20")


async def test_a_dynamic_zone_charges_the_courier_price():
    priced = await _price(zone=_zone("0.00", "dynamic"), estimate=_estimate("26.40"))

    assert priced.base_fee == Decimal("27.00")
    assert priced.is_dynamic is True
    assert priced.serviceable is True


async def test_a_pin_outside_every_zone_is_priced_dynamically():
    """
    A gap in the map is the case we know least about. Asking the courier beats
    a flat number picked before anyone had seen the address.
    """
    priced = await _price(zone=None, estimate=_estimate("88.30"))

    assert priced.base_fee == Decimal("89.00")
    assert priced.is_dynamic is True


# ── no price at all ───────────────────────────────────────────────────────────


async def test_a_dynamic_pin_with_no_quote_is_unserviceable():
    priced = await _price(
        zone=_zone("0.00", "dynamic"),
        estimate=None,
        error="Address is outside the courier's service area",
    )

    assert priced.serviceable is False
    assert priced.base_fee is None
    assert priced.fee is None


async def test_an_unserviceable_pin_is_unserviceable_even_free():
    """
    Free delivery is a discount, not a delivery. A basket over the threshold to
    an address nobody will carry to is still an address nobody will carry to.
    """
    priced = await _price(
        zone=_zone("0.00", "dynamic"),
        estimate=None,
        error="Courier is down",
        subtotal="500.00",
    )

    assert priced.serviceable is False
    assert priced.fee is None


async def test_a_fixed_fee_zone_is_never_unserviceable():
    """
    Its price never came from the courier, so a courier that will not answer
    tells us nothing about whether we can deliver. That is a dispatch problem
    for an admin, not a checkout failure for a customer.
    """
    priced = await _price(
        zone=_zone("15.00", "static"), estimate=None, error="Courier is down"
    )

    assert priced.serviceable is True
    assert priced.fee == Decimal("15.00")


async def test_no_courier_configured_falls_back_rather_than_closing_the_country():
    """
    A missing credential is a fact about us, not about the customer's address.
    Refusing every dynamic order because of one would take the whole country
    offline the moment a secret expired.
    """
    priced = await _price(zone=_zone("0.00", "dynamic"), estimate=None, enabled=False)

    assert priced.serviceable is True
    assert priced.base_fee == SETTINGS.default_delivery_fee


# ── before there is a pin ─────────────────────────────────────────────────────


async def test_without_a_pin_there_is_no_fee_and_no_refusal():
    priced = await _price(
        zone=None, estimate=None, latitude=None, longitude=None, enabled=True
    )

    assert priced.serviceable is True
    assert priced.base_fee is None
    assert priced.fee is None


async def test_without_a_pin_a_qualifying_basket_is_still_free():
    """
    What delivery *would* have cost is unknown; what it costs is not. Showing
    "—" to someone who has already earned free delivery reads as a fee we have
    not told them yet.
    """
    priced = await _price(
        zone=None,
        estimate=None,
        latitude=None,
        longitude=None,
        subtotal="250.00",
    )

    assert priced.fee == Decimal("0.00")
    assert priced.base_fee is None


# ── the callers ───────────────────────────────────────────────────────────────


async def test_calculate_fee_refuses_an_unserviceable_pin():
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service,
            "price",
            new=AsyncMock(
                return_value=delivery_service.DeliveryPrice(
                    zone=None,
                    base_fee=None,
                    free_applied=False,
                    serviceable=False,
                    is_dynamic=True,
                )
            ),
        ),
        pytest.raises(delivery_service.UnserviceableAreaError),
    ):
        await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal("100.00"),
            AsyncMock(),
            latitude=25.1,
            longitude=55.2,
        )


async def test_the_refusal_reaches_the_shopper_as_a_400():
    """
    A 500 tells someone standing at a checkout nothing they can act on. This
    one carries the sentence they need: try another address, or collect.
    """
    error = delivery_service.UnserviceableAreaError()

    assert error.status_code == 400
    assert "different address" in error.detail
    for leak in ("lalamove", "courier", "quotation"):
        assert leak not in error.detail.lower()


async def test_pickup_never_asks_a_courier_anything():
    price = AsyncMock()
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(delivery_service, "price", new=price),
    ):
        fee = await delivery_service.calculate_fee(
            DeliveryMethodEnum.PICKUP, Decimal("100.00"), AsyncMock()
        )

    assert fee == SETTINGS.pickup_fee
    price.assert_not_called()


# ── how long a "no" is allowed to last ────────────────────────────────────────


async def test_a_refusal_is_cached_far_more_briefly_than_a_price():
    """
    A cached price is a saved API call. A cached refusal is a customer being
    told we do not deliver to their street — so if the courier answers again a
    few seconds later, reloading the page has to be a real retry rather than
    two minutes of a stale "no".
    """
    calls = []

    async def create_quotation(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise LalamoveError("Service unavailable")
        return {
            "quotationId": "q_2",
            "priceBreakdown": {"total": "26.40", "currency": "AED"},
            "distance": {"value": "12000"},
        }

    pickup = lalamove_service.PickupPoint(
        name="Kitchen",
        phone="+971500000000",
        address="Al Qasimia",
        latitude=25.3304139,
        longitude=55.3736131,
    )

    lalamove_service.clear_caches()
    with (
        patch.object(lalamove_service, "is_enabled", return_value=True),
        patch.object(
            lalamove_service, "resolve_pickup", new=AsyncMock(return_value=pickup)
        ),
        patch.object(
            lalamove_service.provider,
            "create_quotation",
            new=AsyncMock(side_effect=create_quotation),
        ),
        patch.object(lalamove_service, "_FAILURE_CACHE_SECONDS", 0),
    ):
        first, error = await lalamove_service.estimate_for_point(
            AsyncMock(), 25.1, 55.2, "Somewhere"
        )
        second, _ = await lalamove_service.estimate_for_point(
            AsyncMock(), 25.1, 55.2, "Somewhere"
        )

    assert first is None and error
    assert second is not None, "the failure was served from cache after it expired"
    assert second.cost == Decimal("26.40")
    lalamove_service.clear_caches()


async def test_a_price_is_still_cached_across_re_quotes():
    """The checkout re-quotes on every keystroke-ish change; each one must not
    be a courier call."""
    quotation = AsyncMock(
        return_value={
            "quotationId": "q_1",
            "priceBreakdown": {"total": "26.40", "currency": "AED"},
            "distance": {"value": "12000"},
        }
    )
    pickup = lalamove_service.PickupPoint(
        name="Kitchen",
        phone="+971500000000",
        address="Al Qasimia",
        latitude=25.3304139,
        longitude=55.3736131,
    )

    lalamove_service.clear_caches()
    with (
        patch.object(lalamove_service, "is_enabled", return_value=True),
        patch.object(
            lalamove_service, "resolve_pickup", new=AsyncMock(return_value=pickup)
        ),
        patch.object(lalamove_service.provider, "create_quotation", new=quotation),
    ):
        await lalamove_service.estimate_for_point(AsyncMock(), 25.1, 55.2, "Somewhere")
        await lalamove_service.estimate_for_point(AsyncMock(), 25.1, 55.2, "Somewhere")

    assert quotation.await_count == 1
    lalamove_service.clear_caches()
