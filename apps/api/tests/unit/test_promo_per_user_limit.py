"""
`max_uses_per_user` counts the person, not the login (F-ORD-4).

The per-user ceiling counted redemptions against `orders.user_id` alone, and
only ran `if user_id is not None` — so a guest, who gets a fresh `users` row per
session, was never checked at all, and a signed-in customer could clear the cap
by checking out as a guest. A "one use each" code was one use per *session*.

`_redemptions_by` now takes the same identity triple as `orders_placed_by` —
account, email and phone, OR'd — and `validate` runs it whenever the code
carries the ceiling, guest or not. The write-time re-check under a row lock
(F-ORD-4, in `order_service._persist_order`) is exercised by
`tests/integration/test_promo_race.py`.
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
        code="ONEEACH",
        code_ar=None,
        discount_type=DiscountTypeEnum.PERCENTAGE,
        discount_value=Decimal("10"),
        max_discount_amount=None,
        min_order_amount=None,
        max_uses=None,
        max_uses_per_user=1,
        first_orders_limit=None,
        current_uses=0,
        is_active=True,
        valid_from=None,
        valid_until=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _validate(promo, *, redemptions, **identity):
    with (
        patch.object(
            promo_code_service, "find_by_code", new=AsyncMock(return_value=promo)
        ),
        patch.object(
            promo_code_service,
            "_redemptions_by",
            new=AsyncMock(return_value=redemptions),
        ),
    ):
        return await promo_code_service.validate(
            AsyncMock(), "ONEEACH", Decimal("100.00"), **identity
        )


# ── the ceiling now catches a guest ───────────────────────────────────────────


async def test_a_guest_who_has_used_it_is_refused_on_email_or_phone():
    """No account at all, and still caught — the case the old `user_id is not
    None` guard let straight through."""
    result = await _validate(
        _promo(),
        redemptions=1,
        user_id=None,
        email="repeat@example.com",
        phone="+971501234567",
    )
    assert result.valid is False
    assert result.message == "You have already used this code"


async def test_a_first_use_is_allowed():
    result = await _validate(
        _promo(), redemptions=0, user_id=None, email="fresh@example.com"
    )
    assert result.valid is True


async def test_a_code_without_a_per_user_cap_never_asks():
    """The ceiling is opt-in; a coupon without one is not quietly limited, and
    the redemption count is not even run."""
    promo = _promo(max_uses_per_user=None)
    with (
        patch.object(
            promo_code_service, "find_by_code", new=AsyncMock(return_value=promo)
        ),
        patch.object(
            promo_code_service, "_redemptions_by", new=AsyncMock()
        ) as redemptions,
    ):
        result = await promo_code_service.validate(
            AsyncMock(), "ONEEACH", Decimal("100.00"), user_id=uuid.uuid4()
        )
    assert result.valid is True
    redemptions.assert_not_awaited()


# ── the identity triple the count is built from ───────────────────────────────


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
    stmt = db.execute.await_args.args[0]
    return dict(stmt.compile().params)


async def test_redemptions_are_matched_on_email_and_phone_not_only_the_account():
    db = _capture_db(2)
    used = await promo_code_service._redemptions_by(
        db,
        _promo(code="ONEEACH", code_ar="خصم"),
        user_id=None,
        email="  Sara@Example.COM ",
        phone="0501234567",
    )
    assert used == 2

    asked = _asked_for(db)
    # Email folded, phone in both spellings, and the coupon's own codes.
    assert asked["lower_1"] == "sara@example.com"
    assert set(asked["customer_phone_1"]) == {"+971501234567", "0501234567"}
    assert set(asked["promo_code_used_1"]) == {"ONEEACH", "خصم"}


async def test_a_code_with_no_spellings_counts_nothing():
    """A promo with neither code nor code_ar cannot match a redemption; it must
    not fall through to a query with an empty `IN ()`."""
    db = _capture_db(9)
    used = await promo_code_service._redemptions_by(
        db,
        _promo(code=None, code_ar=None),
        user_id=uuid.uuid4(),
        email=None,
        phone=None,
    )
    assert used == 0
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("redemptions", [1, 2, 5])
async def test_past_the_cap_is_refused_for_a_signed_in_customer(redemptions):
    result = await _validate(
        _promo(max_uses_per_user=1), redemptions=redemptions, user_id=uuid.uuid4()
    )
    assert result.valid is False
