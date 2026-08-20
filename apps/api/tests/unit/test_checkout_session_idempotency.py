"""
Opening the same order's payment page twice has to work.

MM-20260820-001: a session created at 05:16, the customer went back, and the two
attempts that followed — 05:18:24 and 05:18:34 — were both refused by Stripe
with `idempotency_error`. No payment page opened either time, so the order was
abandoned and the same basket was placed again from scratch at 05:28 as -002.

The key is `sess_{order_number}`, which is right: one order, one payment page,
whichever tab the customer is on. What was wrong is that the *payload* under it
moved. `Coupon.create` with no id mints a new coupon on every call, so a
discounted order's `discounts` argument was different every time, and Stripe's
rule is that one key carries one payload or it carries nothing.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from stripe._error import IdempotencyError

from app.services.providers.stripe_provider import StripeProvider, coupon_id


def _order(discount: Decimal = Decimal("15.00")) -> SimpleNamespace:
    return SimpleNamespace(
        items=[
            SimpleNamespace(
                unit_price=Decimal("100.00"),
                quantity=1,
                product_name="Mix Brownies Box Of 6",
                product_sku="FG0041",
            )
        ],
        delivery_fee=Decimal("0.00"),
        low_order_fee=Decimal("0.00"),
        discount_amount=discount,
        promo_code_used="NEW",
        total=Decimal("85.00"),
        order_number="MM-20260820-001",
        email="shopper@example.com",
        id="00000000-0000-0000-0000-000000000001",
    )


async def _create(order, stripe_mock):
    provider = StripeProvider()
    with patch.object(provider, "_configure"):
        return await provider.create_session(order)


async def test_the_discount_is_the_same_coupon_every_time():
    """
    The bug, stated as the thing that must not happen again: two attempts at one
    order producing two different `discounts` arguments under one idempotency
    key.
    """
    order = _order()
    seen = []

    with patch("app.services.providers.stripe_provider.stripe") as stripe_mock:
        stripe_mock.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_test", url="https://stripe.test/pay"
        )
        # Every call after the first finds the coupon already there, which is
        # what asking for it by name is for.
        stripe_mock.Coupon.create.side_effect = [
            SimpleNamespace(id=coupon_id(order)),
            _already_exists(),
            _already_exists(),
        ]
        for _ in range(3):
            await _create(order, stripe_mock)
            seen.append(stripe_mock.checkout.Session.create.call_args.kwargs)

    assert {repr(call["discounts"]) for call in seen} == {
        repr([{"coupon": "order-MM-20260820-001"}])
    }
    # And the key they all ride under is still the order's.
    assert {call["idempotency_key"] for call in seen} == {"sess_MM-20260820-001"}


async def test_an_undiscounted_order_sends_no_coupon_at_all():
    order = _order(discount=Decimal("0.00"))

    with patch("app.services.providers.stripe_provider.stripe") as stripe_mock:
        stripe_mock.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_test", url="https://stripe.test/pay"
        )
        await _create(order, stripe_mock)

    assert stripe_mock.Coupon.create.call_count == 0
    assert stripe_mock.checkout.Session.create.call_args.kwargs["discounts"] == []


async def test_a_coupon_stripe_refuses_still_opens_a_payment_page():
    """
    Full price is wrong and a person can fix it. No payment page is a customer
    who leaves, and nobody finds out.
    """
    order = _order()

    with patch("app.services.providers.stripe_provider.stripe") as stripe_mock:
        stripe_mock.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_test", url="https://stripe.test/pay"
        )
        stripe_mock.Coupon.create.side_effect = _boom()
        session = await _create(order, stripe_mock)

    assert session.checkout_url == "https://stripe.test/pay"
    assert stripe_mock.checkout.Session.create.call_args.kwargs["discounts"] == []


async def test_a_refused_key_opens_a_fresh_session_rather_than_stranding_anybody():
    """
    The backstop under the fix. Any future drift in this payload would otherwise
    reproduce exactly what MM-20260820-001 hit — a checkout button that answers
    400 and a customer with nowhere to go.
    """
    order = _order()

    with patch("app.services.providers.stripe_provider.stripe") as stripe_mock:
        stripe_mock.Coupon.create.return_value = SimpleNamespace(id=coupon_id(order))
        stripe_mock.checkout.Session.create.side_effect = [
            IdempotencyError("Keys for idempotent requests can only be used with…"),
            SimpleNamespace(id="cs_second", url="https://stripe.test/pay-again"),
        ]
        session = await _create(order, stripe_mock)

    assert session.session_id == "cs_second"
    keys = [
        call.kwargs["idempotency_key"]
        for call in stripe_mock.checkout.Session.create.call_args_list
    ]
    assert keys[0] == "sess_MM-20260820-001"
    assert keys[1].startswith("sess_MM-20260820-001_") and keys[1] != keys[0]


def _already_exists():
    from stripe._error import InvalidRequestError

    error = InvalidRequestError("Coupon already exists.", param="id", code=None)
    error.code = "resource_already_exists"
    return error


def _boom():
    from stripe._error import StripeError

    return StripeError("Stripe is having a bad afternoon")
