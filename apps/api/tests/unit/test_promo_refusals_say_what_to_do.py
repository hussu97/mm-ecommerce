"""
Every way a coupon can be refused, and the sentence the customer reads for it.

`create_order` used to raise all of them as `Promo code: {message}`. Two things
were wrong with that. The prefix duplicated a message that already said "code"
— "Promo code: Promo code is not active" reached a real toast — and, worse, it
gave nine unrelated refusals one identical opening. A customer who reads "Promo
code: ..." concludes they mistyped, and retypes a code that was expired, spent,
or never theirs to begin with.

So the messages themselves have to carry the reason, and there is exactly one
place they are written: `promo_code_service.validate`. This file is the list.
Each case says what happened and, where the customer can do something about it,
what — and is short enough to fit in a toast without an internal id in it.
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.order import DeliveryMethodEnum
from app.models.promo_code import DiscountTypeEnum
from app.services import promo_code_service


_NOW = datetime.now(timezone.utc)


def _promo(**over):
    base = dict(
        code="SAVE15",
        code_ar=None,
        discount_type=DiscountTypeEnum.PERCENTAGE,
        discount_value=Decimal("15"),
        max_discount_amount=None,
        min_order_amount=None,
        max_uses=None,
        max_uses_per_user=None,
        first_orders_limit=None,
        requires_phone_verification=False,
        current_uses=0,
        is_active=True,
        valid_from=None,
        valid_until=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _validate(
    promo,
    *,
    subtotal="100.00",
    placed=0,
    redeemed=0,
    phone_verified=True,
    **kwargs,
):
    with (
        patch.object(
            promo_code_service, "find_by_code", new=AsyncMock(return_value=promo)
        ),
        patch.object(
            promo_code_service, "orders_placed_by", new=AsyncMock(return_value=placed)
        ),
        patch.object(
            promo_code_service, "_redemptions_by", new=AsyncMock(return_value=redeemed)
        ),
        patch.object(
            promo_code_service.firebase_auth_service,
            "is_phone_verified",
            new=AsyncMock(return_value=phone_verified),
        ),
    ):
        return await promo_code_service.validate(
            AsyncMock(), "SAVE15", Decimal(subtotal), **kwargs
        )


# ── the list ──────────────────────────────────────────────────────────────────


async def test_a_code_we_do_not_have_says_so_and_suggests_the_fix():
    result = await _validate(None)
    assert result.valid is False
    assert result.message == (
        "We don't recognise that code — check the spelling and try again"
    )


async def test_a_switched_off_code_is_not_a_typo():
    """
    Distinct from "we don't recognise that" on purpose: the code is real, the
    offer is over, and telling somebody to check their spelling would send them
    round a loop they cannot win.
    """
    result = await _validate(_promo(is_active=False))
    assert result.valid is False
    assert result.message == "This code is no longer available"


async def test_a_campaign_that_has_not_opened_yet():
    result = await _validate(_promo(valid_from=_NOW + timedelta(days=2)))
    assert result.valid is False
    assert result.message == (
        "This code isn't active yet — try again once the offer opens"
    )


async def test_the_message_for_an_unopened_campaign_carries_no_date():
    """
    `valid_from` is stored in UTC and the shop runs four hours ahead, so an
    offer opening at 01:00 local would be announced with yesterday's date. A
    vague message beats a wrong fact the customer cannot argue with.
    """
    opens = _NOW + timedelta(days=2)
    result = await _validate(_promo(valid_from=opens))
    assert str(opens.year) not in (result.message or "")


async def test_an_expired_campaign():
    result = await _validate(_promo(valid_until=_NOW - timedelta(minutes=1)))
    assert result.valid is False
    assert result.message == "This code has expired"


async def test_a_campaign_whose_allocation_is_gone():
    """
    The shop's ceiling, not this customer's. "Reached its usage limit" read as
    an accusation and sent people hunting for an order of their own.
    """
    result = await _validate(_promo(max_uses=500, current_uses=500))
    assert result.valid is False
    assert result.message == "This code has been fully claimed"
    assert "you" not in result.message.lower()


async def test_a_customer_who_has_already_redeemed_this_code():
    result = await _validate(
        _promo(max_uses_per_user=1),
        redeemed=1,
        user_id=uuid.uuid4(),
    )
    assert result.valid is False
    assert result.message == "You have already used this code"


async def test_an_acquisition_code_offered_by_a_returning_customer():
    result = await _validate(_promo(first_orders_limit=1), placed=4)
    assert result.valid is False
    assert result.message == "This code is for new customers only"


async def test_a_basket_below_the_threshold_is_told_the_gap_not_the_threshold():
    """
    The one refusal on this list the customer can clear without leaving the
    page — so it has to state the addition, not set the subtraction. The old
    wording ("Minimum order amount of 150 AED required") made them find their
    own subtotal and work it out.
    """
    result = await _validate(_promo(min_order_amount=Decimal("150.00")))
    assert result.valid is False
    assert result.message == "Add AED 50 more to your basket to use this code"


async def test_the_gap_keeps_its_fils_when_there_are_any():
    result = await _validate(
        _promo(min_order_amount=Decimal("149.50")), subtotal="100.00"
    )
    assert result.message == "Add AED 49.50 more to your basket to use this code"


async def test_an_unverified_phone_on_a_gated_delivery():
    """
    The refusal the checkout can act on, and the only one that also sets a flag
    — `create_order` reads it to say "your mobile number", because that is the
    field with the button next to it.
    """
    result = await _validate(
        _promo(requires_phone_verification=True),
        phone_verified=False,
        phone="+971501234567",
        delivery_method=DeliveryMethodEnum.DELIVERY,
        enforce_phone_verification=True,
    )
    assert result.valid is False
    assert result.message == "Verify your phone number to use this code"
    assert result.requires_phone_verification is True
    assert result.phone_verified is False


# ── properties every one of them has to hold ──────────────────────────────────


_EVERY_REFUSAL = [
    (None, {}),
    (_promo(is_active=False), {}),
    (_promo(valid_from=_NOW + timedelta(days=2)), {}),
    (_promo(valid_until=_NOW - timedelta(minutes=1)), {}),
    (_promo(max_uses=500, current_uses=500), {}),
    (_promo(max_uses_per_user=1), {"redeemed": 1, "user_id": uuid.uuid4()}),
    (_promo(first_orders_limit=1), {"placed": 4}),
    (_promo(min_order_amount=Decimal("150.00")), {}),
    (
        _promo(requires_phone_verification=True),
        {
            "phone_verified": False,
            "phone": "+971501234567",
            "delivery_method": DeliveryMethodEnum.DELIVERY,
            "enforce_phone_verification": True,
        },
    ),
]


@pytest.mark.parametrize("promo,extra", _EVERY_REFUSAL)
async def test_no_two_refusals_read_the_same(promo, extra):
    result = await _validate(promo, **extra)
    assert result.valid is False
    assert result.message


async def test_every_refusal_is_distinct():
    """
    Nine reasons, nine sentences. A duplicate here means a customer is being
    told something that is true of a different rule than the one that stopped
    them.
    """
    messages = [await _validate(p, **extra) for p, extra in _EVERY_REFUSAL]
    texts = [m.message for m in messages]
    assert len(set(texts)) == len(texts)


@pytest.mark.parametrize("promo,extra", _EVERY_REFUSAL)
async def test_a_refusal_fits_in_a_toast_and_names_no_internals(promo, extra):
    """
    Read verbatim by the basket and by the pay button, so the constraints are
    the toast's: one short line, no ids, no column names, no SQL words. The
    length bound is generous — it is here to catch a stack trace or a code id
    leaking into the copy, not to police prose.
    """
    message = (await _validate(promo, **extra)).message
    assert len(message) <= 80, message
    assert "\n" not in message
    lowered = message.lower()
    for jargon in ("promo_code", "user_id", "null", "none", "error", "invalid"):
        assert jargon not in lowered, message
