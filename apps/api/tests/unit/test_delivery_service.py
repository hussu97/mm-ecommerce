"""
What `calculate_fee` charges, now that only the pin decides.

These used to be region-rate tests: a slug went in, a table lookup came out.
The slug is gone, and with it the class of bug where a customer in Al Nahda
picked "Dubai", paid the Dubai fee, and was delivered to Sharjah. What is left
is the order the three rules apply in — pickup, then free delivery, then the
zone — because getting that order wrong is how a AED 250 basket ends up being
charged for delivery.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import delivery_service
from app.services.delivery_service import calculate_fee
from app.services.delivery_zone_service import Zone

SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("150.00"),
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
)

SHARJAH_CITY = Zone(
    id=None,  # type: ignore[arg-type]
    name="Sharjah City",
    delivery_fee=Decimal("15.00"),
    fulfilment_provider="lalamove",
    min_lat=25.0,
    max_lat=25.6,
    min_lng=55.2,
    max_lng=55.7,
    rings=(),
)

#: Somewhere inside Sharjah City. The zone lookup is patched, so the exact
#: numbers only have to be plausible.
PIN = {"latitude": 25.3304, "longitude": 55.3710}


def _db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    return db


def _with_zone(zone: Zone | None):
    return (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=zone),
        ),
    )


async def fee(subtotal: str, *, zone: Zone | None = SHARJAH_CITY, pin: bool = True):
    settings_patch, zone_patch = _with_zone(zone)
    with settings_patch, zone_patch:
        return await calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal(subtotal),
            _db(),
            **(PIN if pin else {}),
        )


# ── pickup ────────────────────────────────────────────────────────────────────


async def test_pickup_is_free_whatever_the_basket():
    settings_patch, zone_patch = _with_zone(SHARJAH_CITY)
    with settings_patch, zone_patch:
        for subtotal in ("1.00", "149.99", "1000.00"):
            assert await calculate_fee(
                DeliveryMethodEnum.PICKUP, Decimal(subtotal), _db(), **PIN
            ) == Decimal("0.00")


# ── free delivery ─────────────────────────────────────────────────────────────


async def test_free_at_exactly_the_threshold():
    """150 is the promise. Charging at exactly 150 would make it a lie."""
    assert await fee("150.00") == Decimal("0.00")


async def test_free_above_the_threshold():
    assert await fee("250.00") == Decimal("0.00")


async def test_a_penny_short_still_pays():
    assert await fee("149.99") == Decimal("15.00")


async def test_the_threshold_is_one_number_but_not_one_promise():
    """
    The figure never varies by address — a threshold that did would be the one
    place the delivery map became visible to the customer. Where the offer
    *lands* very much does vary: it reaches the zones whose fee we set, and
    stops there. Beyond them the fee is a courier bill that is the same 90
    dirhams whether the basket is 50 or 500, so there is nothing to waive.
    """
    near = Zone(
        id=None,  # type: ignore[arg-type]
        name="Sharjah City",
        delivery_fee=Decimal("15.00"),
        fulfilment_provider="lalamove",
        min_lat=25.0,
        max_lat=25.6,
        min_lng=55.2,
        max_lng=55.7,
        rings=(),
        pricing_mode="static",
    )
    far = Zone(
        id=None,  # type: ignore[arg-type]
        name="Fujairah",
        delivery_fee=Decimal("0.00"),
        fulfilment_provider="lalamove",
        min_lat=24.8,
        max_lat=25.7,
        min_lng=55.9,
        max_lng=56.4,
        rings=(),
        pricing_mode="dynamic",
    )

    assert await fee("250.00", zone=near) == Decimal("0.00")
    assert await fee("250.00", zone=far) != Decimal("0.00"), (
        "a large basket bought free delivery in a zone we do not price"
    )


# ── the zone ──────────────────────────────────────────────────────────────────


async def test_the_zone_under_the_pin_sets_the_fee():
    assert await fee("100.00") == Decimal("15.00")


async def test_a_pin_outside_every_zone_pays_the_default():
    """A real address we have simply not drawn yet. Quoting nothing is worse."""
    assert await fee("100.00", zone=None) == Decimal("50.00")


async def test_an_order_with_no_coordinates_pays_the_default():
    """
    There is no emirate to fall back on any more, and there should not be: an
    order without a pin cannot be delivered, so the default is the honest
    answer rather than a guess dressed up as a lookup.
    """
    assert await fee("100.00", pin=False) == Decimal("50.00")


# ── the shape of the public rates ─────────────────────────────────────────────


async def test_the_public_rates_no_longer_publish_a_price_list():
    """
    A list of areas and prices would invite the storefront to guess a fee from
    an address string, which is exactly the guess the polygon map replaced.
    """
    with patch.object(
        delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
    ):
        rates = await delivery_service.get_delivery_rates(_db())

    assert set(rates) == {"free_threshold", "pickup_fee", "default_delivery_fee"}
    assert rates["free_threshold"] == 150.0


@pytest.mark.parametrize("gone", ["get_active_regions", "get_all_regions"])
def test_the_region_helpers_are_gone(gone):
    assert not hasattr(delivery_service, gone)
