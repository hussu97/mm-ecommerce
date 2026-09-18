"""
Where a website order reads the customer's name and number from.

MM-20260918-004: a cash store-pickup order reached the counter with no name and
no phone. The order carried both a `pickup_contact` (the collector) and a
`shipping_address` (an older web bundle sent one even for pickup), and the
resolver keyed off "is there an address?" first — so it took the delivery
branch, dropped `customer_name`, and read the phone off an address that was
empty. The counter had nobody to call.

Fixed by reading the fulfilment *method*: a pickup reads `pickup_contact`
whatever else tags along.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.order import DeliveryMethodEnum
from app.services.orders.order_service import _resolve_contact


def _pickup_contact(first="Aisha", last="Khan", phone="+971501234567"):
    return SimpleNamespace(first_name=first, last_name=last, phone=phone)


def _address(phone="+971559876543"):
    return SimpleNamespace(phone=phone)


def _data(method, *, pickup_contact=None, shipping_address=None):
    return SimpleNamespace(
        delivery_method=method,
        pickup_contact=pickup_contact,
        shipping_address=shipping_address,
    )


def test_pickup_reads_the_pickup_contact_even_with_a_stray_address():
    """The MM-20260918-004 shape: pickup method, contact present, and a
    tag-along address that must not win."""
    data = _data(
        DeliveryMethodEnum.PICKUP,
        pickup_contact=_pickup_contact(),
        shipping_address=_address(phone=""),
    )

    name, phone, country, _type = _resolve_contact(data)

    assert name == "Aisha Khan"
    assert phone == "+971501234567"
    assert country == "AE"


def test_pickup_with_only_its_contact():
    data = _data(DeliveryMethodEnum.PICKUP, pickup_contact=_pickup_contact())
    name, phone, _country, _type = _resolve_contact(data)
    assert name == "Aisha Khan"
    assert phone == "+971501234567"


def test_delivery_reads_the_address_phone_and_leaves_the_name_to_the_snapshot():
    data = _data(DeliveryMethodEnum.DELIVERY, shipping_address=_address())
    name, phone, _country, _type = _resolve_contact(data)
    assert name is None
    assert phone == "+971559876543"


def test_pickup_contact_on_a_non_pickup_order_is_still_used():
    """Defensive fallback: a contact carried without the pickup method is the
    best number we have, so it is not thrown away."""
    data = _data(DeliveryMethodEnum.DELIVERY, pickup_contact=_pickup_contact())
    name, phone, _country, _type = _resolve_contact(data)
    assert name == "Aisha Khan"
    assert phone == "+971501234567"


def test_nothing_given_is_all_none():
    data = _data(DeliveryMethodEnum.PICKUP)
    assert _resolve_contact(data) == (None, None, None, None)
