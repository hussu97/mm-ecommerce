"""
The pre-basket delivery answer.

`/delivery/area` exists so a homepage banner and a product card can say
something true about delivery before a cart exists. Two things about it are easy
to get wrong:

  * it must not leak the courier, exactly as `/quote` must not. `speed` is the
    one place a carrier could show through, so it is asserted to be a promise
    and never a mechanism;
  * it must report the *zone's* free-delivery threshold, not the national one,
    or the banner counts down to a number nothing charges against.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.delivery import DeliveryAreaResponse, _speed_of, delivery_area
from app.models.delivery_settings import DeliverySettings
from app.services.delivery_zone_service import Zone

SETTINGS = DeliverySettings(
    pickup_fee=Decimal("0.00"),
)


def _zone(provider="lalamove", *, fee="20.00", threshold="75.00", eligible=True):
    return Zone(
        id=uuid.uuid4(),
        name="Dubai Near",
        delivery_fee=Decimal(fee),
        fulfilment_provider=provider,
        min_lat=24.0,
        max_lat=26.0,
        min_lng=54.0,
        max_lng=57.0,
        rings=(),
        free_delivery_eligible=eligible,
        free_delivery_threshold=None if threshold is None else Decimal(threshold),
    )


async def _area(zone):
    with (
        patch(
            "app.api.v1.delivery.delivery_zone_service.find_zone",
            new=AsyncMock(return_value=zone),
        ),
        patch(
            "app.api.v1.delivery.delivery_service.get_settings",
            new=AsyncMock(return_value=SETTINGS),
        ),
    ):
        # The undecorated handler. `@limiter.limit` insists on a real starlette
        # `Request`, and constructing one here would test slowapi rather than
        # the answer this endpoint gives.
        return await delivery_area.__wrapped__(
            request=AsyncMock(), latitude=25.2, longitude=55.3, db=AsyncMock()
        )


# ── the promise ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("noon_send", "express"),
        ("lalamove", "same_day"),
        ("third_party", "next_day"),
    ],
)
def test_each_kind_of_zone_gets_its_own_promise(provider, expected):
    assert _speed_of(_zone(provider)) == expected


def test_an_unknown_pin_gets_the_most_cautious_promise():
    """Not "we don't know" — a customer reading a homepage needs a sentence, and
    the safe sentence is the slowest one we ever mean."""
    assert _speed_of(None) == "next_day"


async def test_the_response_names_no_courier():
    """Same rule as the quote. `speed` is the one field a carrier could show
    through, and it may only ever describe how fast, never who."""
    for provider in ("noon_send", "lalamove", "third_party"):
        result = await _area(_zone(provider))
        blob = result.model_dump_json().lower()
        for leak in ("lalamove", "noon", "rod", "courier", "third_party", "provider"):
            assert leak not in blob, f"the area response mentions {leak!r}"


# ── the threshold ─────────────────────────────────────────────────────────────


async def test_it_reports_the_zones_own_threshold():
    result = await _area(_zone(threshold="75.00"))
    assert result.free_threshold == 75.0


async def test_a_zone_without_one_names_no_threshold():
    result = await _area(_zone(threshold=None))
    # No national number to stand in with any more. A zone that names no
    # threshold makes no offer, and the storefront shows no countdown rather
    # than a figure that applies nowhere.
    assert result.free_threshold is None


async def test_a_free_zone_reports_zero_rather_than_nothing():
    """Sharjah is free at any basket. Zero is a real threshold; treating it as
    "unset" would have the banner promise free delivery over 150 in the one
    zone where it is always free."""
    result = await _area(_zone("noon_send", fee="0.00", threshold="0.00"))
    assert result.free_threshold == 0.0
    assert result.delivery_fee == 0.0


# ── outside the map ───────────────────────────────────────────────────────────


async def test_a_pin_in_no_zone_is_not_serviceable():
    """
    The active map tiles the whole country, so a pin matching nothing is outside
    it rather than an address we have not drawn. `/area`, `/quote` and order
    creation all say the same thing about the same pin, which is the point —
    a card promising delivery somewhere the checkout will refuse is worse than
    either answer alone.
    """
    result = await _area(None)
    assert result.serviceable is False
    assert result.zone_name is None
    assert result.delivery_fee is None
    assert result.free_delivery_available is False
    assert result.speed == "next_day"


def test_the_response_model_carries_nothing_else():
    """The boundary is the model, so it is checked by name — the same guard the
    quote response has."""
    assert set(DeliveryAreaResponse.model_fields) == {
        "serviceable",
        "zone_name",
        "delivery_fee",
        "free_threshold",
        "free_delivery_available",
        "speed",
    }
