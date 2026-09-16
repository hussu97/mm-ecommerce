"""F-COU-16: a dynamic zone is billed the fee the customer was shown.

`compute_order_totals(strict=True)` re-prices delivery from the pin, and in a
dynamic zone that is a fresh courier quote — a different call, seconds later,
than the one the checkout showed. `reconcile_dynamic_delivery_fee` honours the
quote parked on the basket when it is still the same recent pin, and refuses
(so the checkout re-previews) when nothing proves the customer agreed to the fee
now on the order. Fixed-fee zones re-price deterministically and never reach it.

Pure arithmetic over the two frozen dataclasses plus the cart's quote columns —
no DB, no courier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.cart import Cart
from app.services.delivery import delivery_service
from app.services.orders import order_pricing
from app.services.orders.order_pricing import OrderTotals

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
PIN_LAT = Decimal("25.204849")
PIN_LNG = Decimal("55.270783")


def _dynamic_totals(*, base_fee: Decimal, free_applied: bool = False) -> OrderTotals:
    """A priced dynamic-zone order: subtotal 100, no discount, no small-basket
    fee. `delivery_fee` follows `free_applied` exactly as `compute_order_totals`
    would leave it."""
    fee = Decimal("0.00") if free_applied else base_fee
    priced = delivery_service.DeliveryPrice(
        zone=None,
        base_fee=base_fee,
        free_applied=free_applied,
        free_available=True,
        serviceable=True,
        is_dynamic=True,
        free_threshold=Decimal("150.00"),
    )
    return OrderTotals(
        subtotal=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        delivery_fee=fee,
        low_order_fee=Decimal("0.00"),
        vat_rate=Decimal("0.05"),
        vat_amount=Decimal("4.76"),
        total_excl_vat=Decimal("95.24"),
        total=Decimal("100.00") + fee,
        taxes=[],
        zone=None,
        delivery=priced,
        delivery_fee_known=True,
        serviceable=True,
    )


def _cart(
    *, cost: Decimal | None, at: datetime | None, lat=PIN_LAT, lng=PIN_LNG
) -> Cart:
    cart = Cart()
    cart.delivery_quote_cost = cost
    cart.delivery_quote_at = at
    cart.delivery_quote_latitude = lat
    cart.delivery_quote_longitude = lng
    return cart


def _reconcile(totals: OrderTotals, cart: Cart) -> OrderTotals:
    return order_pricing.reconcile_dynamic_delivery_fee(
        totals, cart, latitude=PIN_LAT, longitude=PIN_LNG, now=NOW
    )


def test_an_unchanged_requote_leaves_the_order_alone():
    # Fresh quote cost 26.40 → rounds up to AED 27, the same figure the order was
    # just priced at. Nothing to reconcile.
    totals = _dynamic_totals(base_fee=Decimal("27.00"))
    cart = _cart(cost=Decimal("26.40"), at=NOW - timedelta(minutes=5))
    assert _reconcile(totals, cart) is totals


def test_a_changed_requote_charges_the_fee_the_customer_saw():
    # The order re-priced at AED 30, but the customer was shown AED 27 (cost
    # 26.40) five minutes ago at this pin. They pay 27, not 30.
    totals = _dynamic_totals(base_fee=Decimal("30.00"))
    cart = _cart(cost=Decimal("26.40"), at=NOW - timedelta(minutes=5))
    out = _reconcile(totals, cart)
    assert out.delivery_fee == Decimal("27.00")
    assert out.total == Decimal("127.00")
    assert out.delivery is not None and out.delivery.base_fee == Decimal("27.00")


def test_free_delivery_survives_the_reconciliation():
    # The basket now qualifies for free delivery, so honouring the shown base fee
    # must not start charging for it again.
    totals = _dynamic_totals(base_fee=Decimal("30.00"), free_applied=True)
    cart = _cart(cost=Decimal("40.00"), at=NOW - timedelta(minutes=1))
    out = _reconcile(totals, cart)
    assert out.delivery_fee == Decimal("0.00")
    assert out.total == Decimal("100.00")
    # The base still reflects what was shown, for the margin record.
    assert out.delivery is not None and out.delivery.base_fee == Decimal("40.00")


def test_a_stale_quote_is_refused():
    totals = _dynamic_totals(base_fee=Decimal("30.00"))
    cart = _cart(cost=Decimal("26.40"), at=NOW - timedelta(minutes=31))
    with pytest.raises(delivery_service.DeliveryPriceChangedError) as exc:
        _reconcile(totals, cart)
    # The message carries the fee the order would otherwise have been billed.
    assert exc.value.new_fee == Decimal("30.00")


def test_a_moved_pin_is_refused():
    totals = _dynamic_totals(base_fee=Decimal("30.00"))
    cart = _cart(
        cost=Decimal("26.40"),
        at=NOW - timedelta(minutes=2),
        lat=Decimal("25.300000"),  # a different address than the order's pin
    )
    with pytest.raises(delivery_service.DeliveryPriceChangedError):
        _reconcile(totals, cart)


def test_a_quote_the_courier_never_priced_is_refused():
    # No stored cost — the quote failed at checkout, so there is no shown fee to
    # honour and the order must re-preview.
    totals = _dynamic_totals(base_fee=Decimal("30.00"))
    cart = _cart(cost=None, at=NOW - timedelta(minutes=1))
    with pytest.raises(delivery_service.DeliveryPriceChangedError):
        _reconcile(totals, cart)


def test_a_naive_quote_timestamp_is_treated_as_utc():
    # A hand-built cart may carry a naive datetime; it must not crash the subtract.
    totals = _dynamic_totals(base_fee=Decimal("30.00"))
    cart = _cart(
        cost=Decimal("26.40"), at=NOW.replace(tzinfo=None) - timedelta(minutes=5)
    )
    out = _reconcile(totals, cart)
    assert out.delivery_fee == Decimal("27.00")
