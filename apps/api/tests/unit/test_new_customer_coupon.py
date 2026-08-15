"""
A new-customer coupon has to know who the customer is.

`max_uses_per_user` has always counted against `orders.user_id`, and guest
checkout mints a fresh `users` row for every session — so the per-customer
ceiling could be cleared by not signing in. A code restricted to somebody's
first three orders would have been worth nothing on exactly the traffic it is
meant to attract.

Identity is therefore the account, the email and the phone, OR'd. Each is
individually escapable and all three together are not worth the effort for a
15% discount capped at AED 30.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.promo_code import DiscountTypeEnum
from app.services import promo_code_service


def _promo(**over):
    base = dict(
        code="WELCOME15",
        code_ar=None,
        discount_type=DiscountTypeEnum.PERCENTAGE,
        discount_value=Decimal("15"),
        max_discount_amount=Decimal("30.00"),
        min_order_amount=None,
        max_uses=None,
        max_uses_per_user=None,
        first_orders_limit=3,
        current_uses=0,
        is_active=True,
        valid_from=None,
        valid_until=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _validate(promo, *, placed: int, subtotal="100.00", **identity):
    with (
        patch.object(
            promo_code_service, "find_by_code", new=AsyncMock(return_value=promo)
        ),
        patch.object(
            promo_code_service, "orders_placed_by", new=AsyncMock(return_value=placed)
        ),
    ):
        return await promo_code_service.validate(
            AsyncMock(), "WELCOME15", Decimal(subtotal), **identity
        )


# ── the rule ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("placed", [0, 1, 2])
async def test_the_first_three_orders_get_it(placed):
    result = await _validate(_promo(), placed=placed)
    assert result.valid is True
    assert result.discount_amount == Decimal("15.00")


@pytest.mark.parametrize("placed", [3, 4, 40])
async def test_the_fourth_does_not(placed):
    result = await _validate(_promo(), placed=placed)
    assert result.valid is False
    assert result.message == "This code is for new customers only"


async def test_the_refusal_does_not_explain_how_we_recognised_them():
    """
    "New customers only" and nothing more. Saying we matched a phone number
    tells a customer both that we keep one and how to avoid it, and neither is
    information the message needs to carry.
    """
    result = await _validate(_promo(), placed=9, phone="+971501234567")
    assert "phone" not in (result.message or "").lower()
    assert "email" not in (result.message or "").lower()


async def test_a_code_without_the_limit_is_unaffected():
    """The rule is opt-in per code; an ordinary coupon does not become an
    acquisition offer because this shipped."""
    result = await _validate(_promo(first_orders_limit=None), placed=50)
    assert result.valid is True


# ── the cap, which is what makes the offer safe to advertise ──────────────────


async def test_the_discount_is_capped():
    result = await _validate(_promo(), placed=0, subtotal="600.00")
    assert result.discount_amount == Decimal("30.00")


async def test_a_small_basket_is_not_raised_to_the_cap():
    result = await _validate(_promo(), placed=0, subtotal="60.00")
    assert result.discount_amount == Decimal("9.00")


# ── identity ──────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def _capture_db(count: int):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(count))
    return db


def _asked_for(db) -> dict:
    """
    The values the count was actually looked up by.

    Read off the compiled statement's bound parameters rather than by matching
    SQL text: the question these tests ask is "which identities went into the
    WHERE clause", and the parameters answer it without pinning the query's
    spelling.
    """
    stmt = db.execute.await_args.args[0]
    return dict(stmt.compile().params)


async def test_no_identity_at_all_counts_nothing():
    """
    A guest who has typed neither an email nor a phone yet is not thereby a
    returning customer. Counting every order ever placed would be the
    alternative, and it would refuse the code to everybody.
    """
    db = _capture_db(999)
    assert (
        await promo_code_service.orders_placed_by(
            db, user_id=None, email=None, phone=None
        )
        == 0
    )
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "identity",
    [
        {"user_id": uuid.uuid4(), "email": None, "phone": None},
        {"user_id": None, "email": "a@b.com", "phone": None},
        {"user_id": None, "email": None, "phone": "+971501234567"},
        {"user_id": uuid.uuid4(), "email": "a@b.com", "phone": "+971501234567"},
    ],
)
async def test_any_single_identity_is_enough_to_count(identity):
    db = _capture_db(4)
    assert await promo_code_service.orders_placed_by(db, **identity) == 4
    db.execute.assert_awaited_once()


# ── identity, now that every checkout carries a real email ────────────────────
#
# `OrderCreate.email` is required, so the email arm of the rule is loaded on
# every web order rather than on the minority who volunteered an address. These
# three are the cases the gate exists to tell apart.


async def test_a_returning_customer_is_refused_on_their_email():
    result = await _validate(
        _promo(), placed=3, email="sara@example.com", phone="+971501234567"
    )
    assert result.valid is False
    assert result.message == "This code is for new customers only"


async def test_the_email_is_matched_case_and_whitespace_insensitively():
    """
    `Sara@Example.com ` and `sara@example.com` are one mailbox. Matching the raw
    string would make the rule a formatting exercise — the same escape the phone
    normalisation exists to close.
    """
    db = _capture_db(3)
    await promo_code_service.orders_placed_by(
        db, user_id=None, email="  Sara@Example.COM ", phone=None
    )
    assert _asked_for(db)["lower_1"] == "sara@example.com"


async def test_the_same_person_under_a_new_email_is_still_caught_by_their_phone():
    """
    The escape a mandatory email does *not* close: addresses are free, and a
    second one costs a customer nothing. The phone is what makes the rule hold
    — it is on the order because a driver has to ring somebody, and both
    spellings of it are searched so retyping the number differently is not an
    escape either.
    """
    db = _capture_db(3)
    await promo_code_service.orders_placed_by(
        db, user_id=None, email="sara.second.address@example.com", phone="0501234567"
    )

    asked = _asked_for(db)
    assert asked["lower_1"] == "sara.second.address@example.com"
    assert set(asked["customer_phone_1"]) == {"+971501234567", "0501234567"}

    # And the count that comes back refuses the coupon, whichever arm matched.
    result = await _validate(
        _promo(),
        placed=3,
        email="sara.second.address@example.com",
        phone="0501234567",
    )
    assert result.valid is False


async def test_a_genuine_first_time_customer_is_allowed():
    result = await _validate(
        _promo(), placed=0, email="new@example.com", phone="+971509999999"
    )
    assert result.valid is True
    assert result.discount_amount == Decimal("15.00")


# ── the placeholder addresses history is full of ──────────────────────────────


async def test_a_guest_placeholder_is_never_an_identity():
    """
    Orders written while email was optional carry `guest-{8 hex}@guest.local`.
    Eight hex characters collide, and a collision here seats one customer inside
    another's history — a first-time buyer told they are not new. A placeholder
    identifies nobody, so it is dropped rather than matched.
    """
    db = _capture_db(999)
    assert (
        await promo_code_service.orders_placed_by(
            db, user_id=None, email="guest-1a2b3c4d@guest.local", phone=None
        )
        == 0
    )
    # No identity left, so no query at all — not a query that would have matched
    # every other guest ever.
    db.execute.assert_not_awaited()


async def test_dropping_the_placeholder_does_not_drop_the_other_identities():
    """It is the email arm that is unusable, not the order."""
    db = _capture_db(2)
    assert (
        await promo_code_service.orders_placed_by(
            db, user_id=None, email="GUEST-1A2B3C4D@guest.local", phone="+971501234567"
        )
        == 2
    )
    asked = _asked_for(db)
    assert "lower_1" not in asked
    assert set(asked["customer_phone_1"]) == {"+971501234567"}


async def test_a_real_address_that_merely_mentions_guest_is_kept():
    """The rule is the domain, not the word."""
    db = _capture_db(1)
    await promo_code_service.orders_placed_by(
        db, user_id=None, email="myguest.local@gmail.com", phone=None
    )
    assert _asked_for(db)["lower_1"] == "myguest.local@gmail.com"


# ── the two spellings are one coupon ──────────────────────────────────────────


async def test_either_spelling_finds_the_same_row():
    promo = _promo(code_ar="خصم15")
    seen: list[str] = []

    async def fake_execute(stmt):
        seen.append(str(stmt))
        return SimpleNamespace(scalar_one_or_none=lambda: promo)

    db = AsyncMock()
    db.execute = fake_execute

    for typed in ("WELCOME15", "خصم15", "  welcome15  "):
        found = await promo_code_service.find_by_code(db, typed)
        assert found is promo
    # One query per lookup, matching either column — not a second round trip
    # that would let the two spellings disagree about which row they found.
    assert len(seen) == 3
    assert "code_ar" in seen[0]


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("  save10 ", "SAVE10"),
        ("SAVE10", "SAVE10"),
        ("خصم15", "خصم15"),
        ("", ""),
    ],
)
def test_a_pasted_code_keeps_its_whitespace_out_of_the_lookup(typed, expected):
    """`.upper()` alone was the rule, and a trailing space matched nothing. The
    storefront trims; the API is a second door and cannot assume that."""
    assert promo_code_service.normalise_code(typed) == expected
