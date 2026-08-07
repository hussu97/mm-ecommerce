"""
Free delivery starts at a different basket in different zones.

One national threshold only held while every zone cost about the same to reach,
and ours do not: a noon Send bike run inside Sharjah costs about AED 14 and a
Lalamove car to the far side of Dubai costs about AED 59. A single number was
therefore withholding an offer that is cheap to honour near the kitchen while
funding one that is not out at the edge.

The change that makes that possible is small and easy to undo by accident: the
`subtotal >= threshold` comparison used to run at the top of `price()`, before
`find_zone` had been called, so it *could not* see the zone. Anyone moving it
back would reintroduce the bug silently — every zone would keep pricing, just
against the wrong number. That is what this file exists to catch.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.services import delivery_service
from app.services.delivery_zone_service import Zone


@pytest.fixture(autouse=True)
def noon_send_is_off(monkeypatch):
    """Same reason as the privacy suite: a real key sends noon Send zones down a
    live estimate path that dies on the mocked session."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "NOON_SEND_API_KEY", "")


NATIONAL = Decimal("150.00")

SETTINGS = DeliverySettings(
    free_delivery_threshold=NATIONAL,
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
)


def _zone(name: str, threshold: Decimal | None, *, eligible: bool = True) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name=name,
        delivery_fee=Decimal("20.00"),
        fulfilment_provider="lalamove",
        min_lat=24.0,
        max_lat=26.0,
        min_lng=54.0,
        max_lng=57.0,
        rings=(),
        free_delivery_eligible=eligible,
        free_delivery_threshold=threshold,
    )


async def _price(subtotal: str, zone: Zone | None):
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
            delivery_service.courier_service,
            "estimate_for_point",
            new=AsyncMock(return_value=(None, None)),
        ),
    ):
        return await delivery_service.price(
            AsyncMock(),
            Decimal(subtotal),
            latitude=25.33,
            longitude=55.37,
        )


# ── the zone's own number wins ────────────────────────────────────────────────


async def test_a_basket_over_the_zone_threshold_is_free_even_though_it_is_under_the_national_one():
    """Dubai goes free at 75. A 100 basket is free there and would not have been
    under the old single 150."""
    priced = await _price("100.00", _zone("Dubai City", Decimal("75.00")))
    assert priced.free_applied is True
    assert priced.fee == Decimal("0.00")
    assert priced.free_threshold == Decimal("75.00")


async def test_a_basket_under_the_zone_threshold_is_not_free_even_though_it_is_over_the_national_one():
    """The far zones go the other way: 200 out there, so a 150 basket that used
    to deliver free now correctly does not."""
    priced = await _price("150.00", _zone("Abu Dhabi", Decimal("200.00")))
    assert priced.free_applied is False
    assert priced.fee == Decimal("20.00")
    assert priced.free_threshold == Decimal("200.00")


async def test_exactly_on_the_zone_threshold_qualifies():
    """`>=`, not `>`. A customer told "free over 75" who spends exactly 75 is
    not going to accept an off-by-one."""
    priced = await _price("75.00", _zone("Dubai City", Decimal("75.00")))
    assert priced.free_applied is True


# ── the fallback ──────────────────────────────────────────────────────────────


async def test_a_zone_with_no_threshold_of_its_own_uses_the_national_one():
    """NULL means "unchanged", which is what every zone meant before the column
    existed. A map that predates it must behave exactly as it did."""
    priced = await _price("150.00", _zone("Legacy Zone", None))
    assert priced.free_applied is True
    assert priced.free_threshold == NATIONAL

    under = await _price("149.99", _zone("Legacy Zone", None))
    assert under.free_applied is False


async def test_zero_is_not_the_same_as_null():
    """A zone may legitimately set zero — Sharjah on noon Send delivers free at
    any basket. Falsiness would collapse that into "use the national 150" and
    quietly start charging the zone we most want free."""
    priced = await _price("5.00", _zone("Sharjah", Decimal("0.00")))
    assert priced.free_applied is True
    assert priced.fee == Decimal("0.00")
    assert priced.free_threshold == Decimal("0.00")


async def test_no_zone_at_all_falls_back_to_the_national_threshold():
    priced = await _price("150.00", None)
    assert priced.free_threshold == NATIONAL


# ── eligibility still gates it ────────────────────────────────────────────────


async def test_a_low_threshold_does_not_override_an_ineligible_zone():
    """The threshold says *when* free delivery applies; `free_delivery_eligible`
    says *whether* it applies at all. A zone that makes no offer must not start
    making one just because someone typed a number into the other column."""
    priced = await _price(
        "500.00", _zone("Third party", Decimal("10.00"), eligible=False)
    )
    assert priced.free_applied is False
    assert priced.free_available is False
    assert priced.fee == Decimal("20.00")


# ── the no-pin case ───────────────────────────────────────────────────────────


async def test_without_a_pin_the_threshold_is_the_national_one_and_says_so():
    """There is no zone to ask yet. The national number is the only honest
    placeholder, and the flag is what stops the storefront presenting it as this
    address's answer."""
    with patch.object(
        delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
    ):
        priced = await delivery_service.price(AsyncMock(), Decimal("50.00"))

    assert priced.free_threshold == NATIONAL
    assert priced.threshold_is_provisional is True
    assert priced.free_available is True


async def test_a_resolved_zone_is_never_provisional():
    priced = await _price("100.00", _zone("Dubai City", Decimal("75.00")))
    assert priced.threshold_is_provisional is False
