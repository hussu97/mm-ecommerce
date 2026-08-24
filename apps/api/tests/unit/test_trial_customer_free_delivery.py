"""
The pilot accounts pay nothing for delivery.

Recovered, near enough verbatim, from the noon Send trial this pattern was
written for. Only the courier and the setting name have changed — the shape of
the risk has not.

This is a discount granted by email address, which is exactly the shape of thing
that leaks. Two ways it could:

**A guest typing the address.** The email box on a guest checkout accepts any
string, so identity has to mean "signed in as", not "claimed to be". Every test
below that grants the discount has a `user_id`; every one that withholds it for
a guest has the right address.

**The quote and the order disagreeing.** The checkout prices through `quote()`
and the order prices through `calculate_fee()` and `compute_order_totals()`. If
only one of them learned about the pilot list, the customer would be shown free
delivery and charged for it anyway — or the reverse. They are checked against
the same inputs here, and `test_delivery_fee_agreement` holds the same line
under both regimes.

And one thing the waiver must never become: **an exemption from geography.** It
is applied after the pin has been priced, so a pilot account still cannot order
somewhere we do not deliver to.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import trial_customer
from app.services.couriers.lalamove_service import Estimate
from app.services.delivery import delivery_service
from app.services.delivery.delivery_zone_service import Zone

TRIAL_EMAIL = "h_abbasi97@hotmail.com"
USER_ID = uuid.uuid4()

SETTINGS = DeliverySettings(
    pickup_fee=Decimal("0.00"),
    low_order_fee=Decimal("0.00"),
    low_order_threshold=None,
)

AJMAN_CITY = Zone(
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


#: What Slider answered for this pin, when Slider is answering at all. The
#: figures are the sandbox's own for Sharjah kitchen to Ajman City.
SLIDER_ESTIMATE = Estimate(
    cost=Decimal("23.60"),
    currency="AED",
    distance_m=15270,
    quotation_id=None,
)


@pytest.fixture(params=["courier unconfigured", "courier configured"], autouse=True)
def regime(request, monkeypatch):
    """Every case in this module, run once with the courier off and once with it on.

    `slider_service.is_enabled()` turns on the presence of a key, and
    `price` asks the courier for an estimate on **every** quote — including in
    the fixed-fee zones, where the number is kept for the margin report and must
    never reach the price. So the pilot being configured moves real code.

    These tests used to read whatever key the developer had in a gitignored
    `.env`: with the pilot set up locally they took the configured branch and
    failed, and CI, which has no key, only ever ran the other one. The branch
    that is live in production was the branch nothing tested.

    Both are pinned here instead, and the assertions are identical across them —
    which is the actual invariant. What a customer is charged, and whether the
    waiver applies, cannot depend on whether a courier credential happens to be
    present. A zone's fee is a property of the zone.

    The estimate is stubbed at the seam between pricing and couriers rather than
    inside Slider, because that is the boundary this module cares about;
    `test_slider_service` owns what happens on the far side of it.
    """
    monkeypatch.setattr(settings, "SLIDER_TRIAL_EMAILS", TRIAL_EMAIL)
    if request.param == "courier unconfigured":
        monkeypatch.setattr(settings, "SLIDER_API_KEY", "")
    else:
        monkeypatch.setattr(settings, "SLIDER_API_KEY", "sk_test_pinned_by_fixture")
        monkeypatch.setattr(
            delivery_service.courier_service,
            "estimate_for_point",
            AsyncMock(return_value=(SLIDER_ESTIMATE, None)),
        )
    return request.param


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
        # The one that matters: a guest who typed the pilot address.
        (None, TRIAL_EMAIL, False),
        (None, None, False),
    ],
)
def test_membership_needs_both_an_account_and_the_address(user_id, email, expected):
    assert trial_customer.is_trial_customer(user_id, email) is expected


def test_an_empty_list_makes_nobody_a_pilot_customer(monkeypatch):
    """
    Ending the pilot must not accidentally make delivery free for everyone —
    and it is the same switch that closes Slider, so both halves stop together.
    """
    monkeypatch.setattr(settings, "SLIDER_TRIAL_EMAILS", "")
    assert not trial_customer.is_trial_customer(USER_ID, TRIAL_EMAIL)


def test_the_list_takes_more_than_one_address(monkeypatch):
    monkeypatch.setattr(
        settings, "SLIDER_TRIAL_EMAILS", f"{TRIAL_EMAIL}, second@example.com"
    )
    assert trial_customer.is_trial_customer(USER_ID, "second@example.com")
    assert trial_customer.is_trial_customer(USER_ID, TRIAL_EMAIL)


# ── what the order is charged ─────────────────────────────────────────────────


def _priced(zone=AJMAN_CITY):
    return (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=SETTINGS)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=zone),
        ),
        # The quote asks the promise resolver, which reads a courier row, a
        # group, a schedule and the branch's hours. This module is about the fee,
        # not about the arrival time.
        patch.object(
            delivery_service.delivery_promise,
            "promise_for_zone",
            new=AsyncMock(return_value=None),
        ),
    )


async def _fee(user_id, email, subtotal="50.00", zone=AJMAN_CITY):
    settings_patch, zone_patch, promise_patch = _priced(zone)
    with settings_patch, zone_patch, promise_patch:
        return await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal(subtotal),
            AsyncMock(),
            latitude=25.4052,
            longitude=55.4384,
            user_id=user_id,
            email=email,
        )


async def test_the_pilot_account_is_charged_nothing():
    assert await _fee(USER_ID, TRIAL_EMAIL) == Decimal("0.00")


async def test_everyone_else_is_charged_the_zone_fee():
    assert await _fee(USER_ID, "someone@example.com") == Decimal("10.00")
    assert await _fee(None, None) == Decimal("10.00")


async def test_a_courier_estimate_never_becomes_a_fixed_zone_s_price(regime):
    """A configured courier must not change what a fixed-fee zone charges.

    `price` asks the courier on every quote, fixed zones included — the answer
    is evidence for the margin report, not a price. Ajman City charges AED 10
    off its own row while Slider quotes AED 23.60 for the same pin, so if the
    estimate ever leaked into the fee it would more than double the charge in
    exactly the zones the pilot is running in, and it would do it the moment a
    credential was added rather than at a deploy anyone was watching.

    Deliberately asserted against the estimate's own figure rather than "not
    the fee": a test that only knows the right answer cannot tell you which
    wrong answer it got.
    """
    if regime != "courier configured":
        pytest.skip("nothing is estimating in the unconfigured regime")
    fee = await _fee(USER_ID, "someone@example.com")
    assert fee == AJMAN_CITY.delivery_fee == Decimal("10.00")
    assert fee != SLIDER_ESTIMATE.cost


async def test_the_zone_is_still_costed_when_the_courier_is_configured(regime):
    """And the estimate is actually asked for.

    The counterpart to the test above. Having established the number must not
    reach the price, the way to satisfy that cheaply would be to stop asking —
    which silently ends the only record of what these zones cost to serve. The
    call is the point; ignoring the answer for pricing is the point.
    """
    if regime != "courier configured":
        pytest.skip("nothing is estimating in the unconfigured regime")
    await _fee(USER_ID, TRIAL_EMAIL)
    delivery_service.courier_service.estimate_for_point.assert_awaited()


async def test_the_waiver_survives_the_courier_being_configured(regime):
    """The pilot account pays nothing whether or not Slider has credentials.

    Both halves of the switch are granted by one setting, and neither is
    conditional on the courier being reachable. A waiver that quietly stopped
    applying when a key was added — or when one expired — would charge the
    pilot account for a delivery the pilot exists to give away.
    """
    assert await _fee(USER_ID, TRIAL_EMAIL) == Decimal("0.00")


async def test_a_guest_at_the_pilot_address_is_charged():
    """The discount is attached to an account, not to a string in a form."""
    assert await _fee(None, TRIAL_EMAIL) == Decimal("10.00")


async def test_an_anonymous_call_still_prices_normally():
    """
    Most callers pass no identity at all — the admin, the register, anything
    that prices without a customer in hand. They must get the ordinary fee
    rather than tripping over a missing argument.
    """
    settings_patch, zone_patch, promise_patch = _priced()
    with settings_patch, zone_patch, promise_patch:
        fee = await delivery_service.calculate_fee(
            DeliveryMethodEnum.DELIVERY,
            Decimal("50.00"),
            AsyncMock(),
            latitude=25.4052,
            longitude=55.4384,
        )
    assert fee == Decimal("10.00")


async def test_an_unserviceable_pin_is_refused_rather_than_made_free():
    """
    Free is a discount, not an exemption from geography. The waiver is applied
    **after** `price`, so a pin nothing can be delivered to is still refused —
    for the pilot account exactly as for anybody else.
    """
    settings_patch, zone_patch, promise_patch = _priced(zone=None)
    with settings_patch, zone_patch, promise_patch:
        with pytest.raises(delivery_service.UnserviceableAreaError):
            await delivery_service.calculate_fee(
                DeliveryMethodEnum.DELIVERY,
                Decimal("50.00"),
                AsyncMock(),
                latitude=23.5880,
                longitude=58.3829,
                user_id=USER_ID,
                email=TRIAL_EMAIL,
            )


# ── what the checkout is shown ────────────────────────────────────────────────


async def _quote(user_id, email, subtotal="50.00"):
    settings_patch, zone_patch, promise_patch = _priced()
    with settings_patch, zone_patch, promise_patch:
        return await delivery_service.quote(
            AsyncMock(),
            Decimal(subtotal),
            latitude=25.4052,
            longitude=55.4384,
            user_id=user_id,
            email=email,
        )


async def test_the_checkout_shows_the_pilot_account_free_delivery():
    result = await _quote(USER_ID, TRIAL_EMAIL)
    assert result["delivery_fee"] == 0.0
    assert result["free_delivery_applied"] is True
    # Otherwise the summary reads "free delivery" and "AED 25 to go" at once.
    assert result["remaining_for_free"] == 0.0
    # Still the real zone price, so the strike-through has something to strike.
    assert result["base_fee"] == 10.0


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


async def test_the_pilot_discount_says_nothing_about_why():
    """
    Free delivery is free delivery. Naming the reason would put "pilot" in front
    of a customer, and the account exists to walk the ordinary path.
    """
    blob = repr(await _quote(USER_ID, TRIAL_EMAIL)).lower()
    for leak in ("trial", "pilot", "slider", "test", "internal"):
        assert leak not in blob, f"the quote mentions {leak!r}"
