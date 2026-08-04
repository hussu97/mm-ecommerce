"""
The trial accounts pay nothing for delivery.

This is a discount granted by email address, which is exactly the shape of thing
that leaks. Two ways it could:

**A guest typing the address.** The email box on a guest checkout accepts any
string, so identity has to mean "signed in as", not "claimed to be". Every test
below that grants the discount has a `user_id`; every one that withholds it for
a guest has the right address.

**The quote and the order disagreeing.** The checkout prices through
`quote()` and the order prices through `calculate_fee()`. If only one of them
learned about the trial list, the customer would be shown free delivery and
charged for it anyway — or the reverse. Both are checked here against the same
inputs.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import delivery_service, trial_customer
from app.services.delivery_zone_service import Zone

TRIAL_EMAIL = "h_abbasi97@hotmail.com"
USER_ID = uuid.uuid4()

SETTINGS = DeliverySettings(
    free_delivery_threshold=Decimal("200.00"),
    pickup_fee=Decimal("0.00"),
    default_delivery_fee=Decimal("50.00"),
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


@pytest.fixture(autouse=True)
def trial_list(monkeypatch):
    monkeypatch.setattr(settings, "TRIAL_CUSTOMER_EMAILS", TRIAL_EMAIL)


# ── the membership test itself ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "user_id,email,expected",
    [
        (USER_ID, TRIAL_EMAIL, True),
        (USER_ID, "H_Abbasi97@Hotmail.com", True),
        (USER_ID, f"  {TRIAL_EMAIL}  ", True),
        # Signed in, but somebody else.
        (USER_ID, "someone@example.com", False),
        (USER_ID, None, False),
        # The one that matters: a guest who typed the trial address.
        (None, TRIAL_EMAIL, False),
        (None, None, False),
    ],
)
def test_membership_needs_both_an_account_and_the_address(user_id, email, expected):
    assert trial_customer.is_trial_customer(user_id, email) is expected


def test_an_empty_list_makes_nobody_a_trial_customer(monkeypatch):
    """Ending the trial must not accidentally make delivery free for everyone."""
    monkeypatch.setattr(settings, "TRIAL_CUSTOMER_EMAILS", "")
    assert not trial_customer.is_trial_customer(USER_ID, TRIAL_EMAIL)


# ── what the order is charged ─────────────────────────────────────────────────


async def _fee(user_id, email, subtotal="100.00"):
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=SHARJAH_CENTRAL),
        ),
    ):
        return await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal(subtotal),
            AsyncMock(),
            latitude=25.3304,
            longitude=55.3736,
            user_id=user_id,
            email=email,
        )


async def test_the_trial_account_is_charged_nothing():
    assert await _fee(USER_ID, TRIAL_EMAIL) == Decimal("0.00")


async def test_everyone_else_is_charged_the_zone_fee():
    assert await _fee(USER_ID, "someone@example.com") == Decimal("15.00")
    assert await _fee(None, None) == Decimal("15.00")


async def test_a_guest_at_the_trial_address_is_charged():
    """The discount is attached to an account, not to a string in a form."""
    assert await _fee(None, TRIAL_EMAIL) == Decimal("15.00")


async def test_an_anonymous_call_still_prices_normally():
    """
    Most callers pass no identity at all — the admin, the POS, anything that
    prices without a customer in hand. They must get the ordinary fee rather
    than tripping over a missing argument.
    """
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=SHARJAH_CENTRAL),
        ),
    ):
        fee = await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal("100.00"),
            AsyncMock(),
            latitude=25.3304,
            longitude=55.3736,
        )
    assert fee == Decimal("15.00")


# ── what the checkout is shown ────────────────────────────────────────────────


async def _quote(user_id, email, subtotal="100.00"):
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=SHARJAH_CENTRAL),
        ),
    ):
        return await delivery_service.quote(
            AsyncMock(),
            Decimal(subtotal),
            latitude=25.3304,
            longitude=55.3736,
            user_id=user_id,
            email=email,
        )


async def test_the_checkout_shows_the_trial_account_free_delivery():
    result = await _quote(USER_ID, TRIAL_EMAIL)
    assert result["delivery_fee"] == 0.0
    assert result["free_delivery_applied"] is True
    # Otherwise the summary reads "free delivery" and "AED 100 to go" at once.
    assert result["remaining_for_free"] == 0.0
    # Still the real zone price, so the strike-through has something to strike.
    assert result["base_fee"] == 15.0


async def test_the_quote_and_the_order_never_disagree():
    """
    The number on the checkout and the number on the order come from two
    different functions. If they drift, the customer is shown one price and
    charged another.
    """
    for user_id, email in (
        (USER_ID, TRIAL_EMAIL),
        (USER_ID, "someone@example.com"),
        (None, TRIAL_EMAIL),
        (None, None),
    ):
        shown = (await _quote(user_id, email))["delivery_fee"]
        charged = float(await _fee(user_id, email))
        assert shown == charged, f"{email} is shown {shown} and charged {charged}"


async def test_the_trial_discount_says_nothing_about_why():
    """
    Free delivery is free delivery. Naming the reason would put "trial" in front
    of a customer, and the account exists to walk the ordinary path.
    """
    blob = repr(await _quote(USER_ID, TRIAL_EMAIL)).lower()
    for leak in ("trial", "noon", "test", "internal"):
        assert leak not in blob, f"the quote mentions {leak!r}"
