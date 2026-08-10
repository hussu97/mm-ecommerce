"""
A quote with no pin is the checkout's *first* call, and it must not be a 500.

The storefront asks what delivery costs before it knows where the order is
going: the page mounts, the basket has a subtotal, no address has been chosen
yet. `price()` answers that honestly — no zone, no fee, and **no threshold**,
because thresholds stopped being national when the outer zones went fixed-fee
and every zone now answers for itself.

`DeliveryQuoteResponse.free_threshold` was left declared as a required `float`
when that changed. FastAPI validates responses against the model, so the null
raised `ResponseValidationError` *after* the handler had done its work
correctly — a 500, on the happy path, on every checkout, reported as a server
fault and logged as one. Found live on 2026-08-10:

    POST /api/v1/delivery/quote {"subtotal":140}   -> 500
    ...same call with a pin                        -> 200

Nothing was wrong with the arithmetic. The contract disagreed with itself, and
only the serialisation boundary knew.

So these tests go through `DeliveryQuoteResponse`, not just the service dict. A
test that asserts on the dict alone passes while production 500s, which is
precisely what happened.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.delivery import DeliveryQuoteResponse
from app.models.delivery_settings import DeliverySettings
from app.services import delivery_service

pytestmark = pytest.mark.asyncio


SETTINGS = DeliverySettings(
    pickup_fee=Decimal("0.00"),
)


async def _quote_without_pin(subtotal="140.00"):
    """Exactly what the checkout sends on mount: a basket, and nothing else."""
    with patch.object(
        delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
    ):
        return await delivery_service.quote(
            AsyncMock(),
            Decimal(subtotal),
            latitude=None,
            longitude=None,
            cart=None,
            address=None,
        )


async def test_a_quote_with_no_pin_serialises():
    """The regression. This is the call that returned 500 in production."""
    result = await _quote_without_pin()

    # The assertion that matters: the model accepts it. Before the fix this
    # raised, and FastAPI turned that into a 500 the customer saw as a broken
    # checkout and Sentry saw as our fault.
    response = DeliveryQuoteResponse(**result)

    assert response.free_threshold is None
    assert response.free_threshold_provisional is True


async def test_no_pin_is_serviceable_rather_than_refused():
    """
    "We don't know yet" is not "we don't deliver there".

    If this ever flips, the checkout will tell a customer their address is
    unserviceable before they have given one.
    """
    response = DeliveryQuoteResponse(**await _quote_without_pin())

    assert response.serviceable is True
    assert response.in_known_zone is False
    assert response.zone_name is None


async def test_no_pin_names_no_price_and_no_countdown():
    """
    Every number is withheld rather than guessed.

    A national default here is what the fixed-fee zones removed: quoting one
    would have the storefront count down to a figure the fee will never be
    calculated against.
    """
    response = DeliveryQuoteResponse(**await _quote_without_pin())

    assert response.delivery_fee is None
    assert response.base_fee is None
    assert response.free_threshold is None
    # Zero, not a countdown from a threshold we do not have.
    assert response.remaining_for_free == 0.0
    assert response.delivery_estimate is None


async def test_the_offer_is_not_withdrawn_before_the_address_is_known():
    """
    `free_delivery_available` stays true with no pin.

    False would mean "free delivery cannot reach here", which is a claim about
    an address nobody has supplied. The basket must not talk a customer out of
    an offer they may well qualify for.
    """
    response = DeliveryQuoteResponse(**await _quote_without_pin())

    assert response.free_delivery_available is True
    assert response.free_delivery_applied is False


async def test_the_threshold_stays_nullable_in_the_contract():
    """
    Guards the field itself, not one response built from it.

    The bug was a type annotation, and a test that only ever builds a valid
    object would not have caught it being narrowed back.
    """
    field = DeliveryQuoteResponse.model_fields["free_threshold"]

    assert not field.is_required(), (
        "free_threshold must stay optional — a quote before a pin has no "
        "threshold to name, and a required float turns that into a 500"
    )
    assert (
        DeliveryQuoteResponse(
            free_delivery_applied=False, remaining_for_free=0.0, in_known_zone=False
        ).free_threshold
        is None
    )
