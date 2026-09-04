"""
The number on the checkout and the number on the order are the same number.

They come from two different functions — `quote()` prices the summary a shopper
reads, `calculate_fee()` prices the row that is written — and nothing but a test
stops them drifting. When they drift, the customer is shown one price and
charged another, which is the kind of bug that arrives as a chargeback.

This file used to be about a trial account that paid nothing for delivery: a
waiver granted by email address, applied in both functions, with tests here to
prove the two agreed about it. The waiver is gone with the rest of the trial
routing. The agreement it was protecting is not, so these are what remain.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services.delivery import delivery_service
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
    # The offer has to reach this zone, or the threshold cases below prove
    # nothing about the two functions agreeing.
    free_delivery_eligible=True,
    free_delivery_threshold=Decimal("150.00"),
)


def _pinned():
    return (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=SHARJAH_CENTRAL),
        ),
        # The quote asks the promise resolver, which reads a courier row, a
        # group, a schedule and the branch's hours. This module is about the fee
        # the quote and the order agree on, not about the arrival time.
        patch.object(
            delivery_service.delivery_promise,
            "promise_for_zone",
            new=AsyncMock(return_value=None),
        ),
    )


async def _fee(subtotal="100.00"):
    settings_patch, zone_patch, promise_patch = _pinned()
    with settings_patch, zone_patch, promise_patch:
        return await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal(subtotal),
            AsyncMock(),
            latitude=25.3304,
            longitude=55.3736,
        )


async def _quote(subtotal="100.00"):
    settings_patch, zone_patch, promise_patch = _pinned()
    with settings_patch, zone_patch, promise_patch:
        return await delivery_service.quote(
            AsyncMock(),
            Decimal(subtotal),
            latitude=25.3304,
            longitude=55.3736,
        )


async def test_the_zone_fee_is_what_everyone_pays():
    """
    The pin decides the price and nothing about the customer does — the identity
    that used to waive it for a trial account is gone, so there is only the one
    answer left, whoever asks.
    """
    assert await _fee() == Decimal("15.00")


async def test_an_anonymous_call_still_prices_normally():
    """
    Most callers pass no identity at all — the admin, the POS, anything that
    prices without a customer in hand. They must get the ordinary fee rather
    than tripping over a missing argument.
    """
    settings_patch, zone_patch, promise_patch = _pinned()
    with settings_patch, zone_patch, promise_patch:
        fee = await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal("100.00"),
            AsyncMock(),
            latitude=25.3304,
            longitude=55.3736,
        )
    assert fee == Decimal("15.00")


async def test_free_delivery_over_the_threshold_reaches_both():
    """The one discount that does exist has to agree with itself as well."""
    shown = await _quote(subtotal="250.00")
    assert shown["delivery_fee"] == 0.0
    assert shown["free_delivery_applied"] is True
    # Otherwise the summary reads "free delivery" and "AED 100 to go" at once.
    assert shown["remaining_for_free"] == 0.0
    # Still the real zone price, so the strike-through has something to strike.
    assert shown["base_fee"] == 15.0
    assert await _fee(subtotal="250.00") == Decimal("0.00")


async def test_the_quote_and_the_order_never_disagree():
    for subtotal in ("100.00", "199.99", "200.00", "250.00"):
        shown = (await _quote(subtotal))["delivery_fee"]
        charged = float(await _fee(subtotal))
        assert shown == charged, f"{subtotal} is shown {shown} and charged {charged}"
