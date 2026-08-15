"""
The small-basket surcharge.

AED 15 on a delivery order whose goods come to AED 35 or less. Three things
about it are easy to get wrong and are pinned here:

  * it is judged on the basket *before* any discount, so a coupon can never
    conjure a fee into existence;
  * it is outside the VAT base, exactly as delivery is;
  * it reaches Stripe. A fee on the order that is missing from the payment
    session charges the customer a different number from the one the order says
    they owe, and nothing notices, because both totals are internally
    consistent.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import order_pricing
from app.services.order_pricing import low_order_fee_for

SETTINGS = DeliverySettings(
    pickup_fee=Decimal("0.00"),
    low_order_fee=Decimal("15.00"),
    low_order_threshold=Decimal("35.00"),
)

DELIVERY = DeliveryMethodEnum.DELIVERY
PICKUP = DeliveryMethodEnum.PICKUP

#: A basket with no tax group on it. `tax_breakdown` charges the standard rate
#: for one rather than selling it VAT-free, so these read 5% without needing a
#: tax table behind them — and `_resolve_tax` short-circuits on a null group,
#: so the mock session below is never actually queried.
NO_GROUP = None


async def _totals(subtotal, discount, *, method=DELIVERY, fee=Decimal("0.00")):
    """Price a one-line basket, with the pin's price stubbed to `fee`."""
    priced = SimpleNamespace(
        serviceable=True, fee=fee, zone=None, estimate=None, error=None
    )
    with patch.object(
        order_pricing.delivery_service,
        "get_settings",
        new=AsyncMock(return_value=SETTINGS),
    ):
        return await order_pricing.compute_order_totals(
            AsyncMock(),
            lines=[(NO_GROUP, subtotal)],
            delivery_method=method,
            discount_amount=discount,
            latitude=Decimal("25.33"),
            longitude=Decimal("55.37"),
            address="somewhere",
            priced=priced,
        )


# ── the threshold ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "subtotal,expected",
    [
        ("10.00", "15.00"),
        ("34.99", "15.00"),
        # "under and including 35" — 35 is a small basket.
        ("35.00", "15.00"),
        ("35.01", "0.00"),
        ("100.00", "0.00"),
    ],
)
def test_the_threshold_is_inclusive(subtotal, expected):
    assert low_order_fee_for(Decimal(subtotal), DELIVERY, SETTINGS) == Decimal(expected)


def test_pickup_never_pays_it():
    """Handing a box over the counter costs us nothing extra, so there is no
    cost for the fee to cover."""
    assert low_order_fee_for(Decimal("10.00"), PICKUP, SETTINGS) == Decimal("0.00")


def test_a_null_threshold_disables_the_fee():
    """Which is what it means on any deploy that has not switched it on."""
    off = DeliverySettings(
        pickup_fee=Decimal("0.00"),
        low_order_fee=Decimal("15.00"),
        low_order_threshold=None,
    )
    assert low_order_fee_for(Decimal("10.00"), DELIVERY, off) == Decimal("0.00")


def test_an_empty_basket_is_not_a_small_order():
    """
    It is a basket that cannot be ordered. The rule came off the checkout when
    the client-side mirror of this function was deleted: `/orders/preview` fires
    while the basket is still loading, and quoting a 15-dirham surcharge against
    a subtotal of zero puts a fee on a page that is about to redirect.
    """
    assert low_order_fee_for(Decimal("0.00"), DELIVERY, SETTINGS) == Decimal("0.00")


# ── the discount interaction, which is the whole design decision ──────────────


async def test_a_coupon_cannot_create_the_fee():
    """
    A 40-dirham basket with a 15% new-customer code is still a 40-dirham basket.

    On the discounted figure it would fall to 34, trip the 35 threshold, and hand
    back a 15-dirham fee against a 6-dirham discount — the acquisition offer
    fighting itself, and the customer watching a fee appear *because* they
    applied a coupon. The fee is judged on the gross basket for exactly this
    reason, and this is the case that would catch anyone switching it.
    """
    totals = await _totals(Decimal("40.00"), Decimal("6.00"))

    assert totals.low_order_fee == Decimal("0.00")
    # 40 - 6 + 0 delivery + 0 fee
    assert totals.total == Decimal("34.00")


# ── VAT ───────────────────────────────────────────────────────────────────────


async def test_the_fee_sits_outside_vat_exactly_as_delivery_does():
    """
    VAT is on goods. A 30-dirham basket pays VAT on 30, not on 30 + 15 + delivery.
    """
    subtotal = Decimal("30.00")
    totals = await _totals(subtotal, Decimal("0.00"), fee=Decimal("20.00"))

    assert totals.low_order_fee == Decimal("15.00")
    assert totals.total == Decimal("65.00")  # 30 goods + 20 delivery + 15 fee
    # VAT back-calculated from the goods alone: 30 / 1.05 * 0.05
    assert totals.vat_amount == Decimal("1.43")
    assert totals.total_excl_vat == Decimal("28.57")
    assert totals.vat_amount + totals.total_excl_vat == subtotal


# ── it must reach the payment session ─────────────────────────────────────────


async def test_stripe_line_items_add_up_to_the_order_total():
    """
    The guard that matters. A fee on the order but not in the session means the
    card is charged less than the order says, the books disagree, and both
    numbers look right in isolation.
    """
    from app.services.providers.stripe_provider import StripeProvider

    order = SimpleNamespace(
        items=[
            SimpleNamespace(
                unit_price=Decimal("30.00"),
                quantity=1,
                product_name="Brookie Cookie Melt",
                product_sku="BCM-1",
            )
        ],
        delivery_fee=Decimal("20.00"),
        low_order_fee=Decimal("15.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("65.00"),
        order_number="MM-TEST-1",
        email="x@example.com",
        id="00000000-0000-0000-0000-000000000001",
    )

    provider = StripeProvider()
    with (
        patch.object(provider, "_configure"),
        patch("app.services.providers.stripe_provider.stripe") as stripe_mock,
    ):
        stripe_mock.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_test", url="https://stripe.test/pay"
        )
        await provider.create_session(order)

    kwargs = stripe_mock.checkout.Session.create.call_args.kwargs
    line_items = kwargs["line_items"]

    charged = sum(
        Decimal(item["price_data"]["unit_amount"]) * item["quantity"]
        for item in line_items
    ) / Decimal("100")
    assert charged == order.total, (
        f"Stripe would charge {charged} against an order total of {order.total}"
    )

    # One combined fee line, not two. The split lives on the checkout and the
    # confirmation email, where the customer can ask what it is for; Stripe just
    # takes the money — but it is still named after everything in it.
    fee_lines = [
        item
        for item in line_items
        if "fee" in item["price_data"]["product_data"]["name"].lower()
    ]
    assert len(fee_lines) == 1
    assert fee_lines[0]["price_data"]["unit_amount"] == 3500  # 20 + 15
    assert fee_lines[0]["price_data"]["product_data"]["name"] == (
        "Delivery & small order fee"
    )


async def test_a_fee_only_order_still_gets_a_line():
    """Delivery can be free while the small-basket fee is not — Sharjah is
    exactly that case. Keying the line on `delivery_fee > 0` would drop the
    charge entirely."""
    from app.services.providers.stripe_provider import StripeProvider

    order = SimpleNamespace(
        items=[
            SimpleNamespace(
                unit_price=Decimal("30.00"),
                quantity=1,
                product_name="Brookie Cookie Melt",
                product_sku="BCM-1",
            )
        ],
        delivery_fee=Decimal("0.00"),
        low_order_fee=Decimal("15.00"),
        discount_amount=Decimal("0.00"),
        total=Decimal("45.00"),
        order_number="MM-TEST-2",
        email="x@example.com",
        id="00000000-0000-0000-0000-000000000002",
    )

    provider = StripeProvider()
    with (
        patch.object(provider, "_configure"),
        patch("app.services.providers.stripe_provider.stripe") as stripe_mock,
    ):
        stripe_mock.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_test", url="https://stripe.test/pay"
        )
        await provider.create_session(order)

    line_items = stripe_mock.checkout.Session.create.call_args.kwargs["line_items"]
    charged = sum(
        Decimal(item["price_data"]["unit_amount"]) * item["quantity"]
        for item in line_items
    ) / Decimal("100")
    assert charged == order.total

    # And it is not called "Delivery Fee". Delivery on this order is free; a
    # payment page reading "Delivery Fee AED 15.00" against free delivery is the
    # discrepancy a customer abandons the checkout over.
    fee_lines = [
        item
        for item in line_items
        if "fee" in item["price_data"]["product_data"]["name"].lower()
    ]
    assert len(fee_lines) == 1
    assert fee_lines[0]["price_data"]["product_data"]["name"] == "Small order fee"
