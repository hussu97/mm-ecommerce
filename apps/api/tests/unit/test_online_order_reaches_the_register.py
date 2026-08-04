"""
A website order has to become something a kitchen can see.

Web and POS orders have always shared one `orders` table, but a storefront order
left every POS column null — so no register, dispatch board or operations screen
could find it. It existed only in the admin, which is no use to somebody who has
to bake it.

What is asserted here is the wiring, and the two places it is easy to get subtly
wrong: the order type (a str-mixin enum whose `str()` is not its value, which
sent delivery orders to the counter labelled as pickups) and the check number
(allocated once, or a re-run silently burns another).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum, OrderTypeEnum, PosOrderStatusEnum
from app.services import pos_order_service


BRANCH = SimpleNamespace(
    id=uuid.uuid4(),
    reference="K001",
    name="Melting Moments Cakes",
    is_active=True,
    deleted_at=None,
)


def _order(**overrides) -> Order:
    order = Order(
        order_number="MM-20260804-001",
        email="customer@example.com",
        delivery_method=DeliveryMethodEnum.DELIVERY,
        subtotal=Decimal("185.00"),
        total=Decimal("200.00"),
        status=OrderStatusEnum.CREATED,
        # Stamped at creation by `_persist_order`, not by the attach — an order
        # that never reaches a register is still a website order.
        source=OrderSourceEnum.ONLINE.value,
        shipping_address_snapshot={
            "first_name": "Hussain",
            "last_name": "Abbasi",
            "phone": "+971501234567",
            "address_line_1": "Al Majaz Waterfront",
            "city": "Sharjah",
        },
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    return order


@pytest.fixture
def attach(monkeypatch):
    """`attach_online_order` with the trading day and check number stubbed."""
    calls = {"days": 0, "checks": 0}

    async def get_or_open(db, branch, **_kwargs):
        calls["days"] += 1
        return SimpleNamespace(business_date="2026-08-04")

    async def next_check_number(db, branch_id, business_date, reset_daily):
        calls["checks"] += 1
        return 7

    async def settings(db):
        return SimpleNamespace(order_number_reset_daily=True)

    monkeypatch.setattr(
        pos_order_service.business_day_service, "get_or_open", get_or_open
    )
    monkeypatch.setattr(pos_order_service, "_next_check_number", next_check_number)
    monkeypatch.setattr(pos_order_service, "_settings", settings)
    return calls


@pytest.mark.asyncio
async def test_it_arrives_on_the_register_as_a_pending_online_order(attach):
    order = _order()
    await pos_order_service.attach_online_order(None, order, BRANCH)

    assert order.branch_id == BRANCH.id
    # `is_pos` is what /pos/orders, the dispatch board and the operations
    # screens all filter on. Without it the order is invisible to every one.
    assert order.is_pos is True
    # Set before this ran; asserted here because the register filters on it.
    assert order.source == OrderSourceEnum.ONLINE.value
    # Pending, not active: nobody at the counter has seen it yet.
    assert order.pos_status == PosOrderStatusEnum.PENDING.value
    assert order.business_date == "2026-08-04"
    assert order.check_number == 7
    assert order.opened_at is not None


@pytest.mark.asyncio
async def test_a_delivery_order_is_not_labelled_a_pickup(attach):
    """
    `DeliveryMethodEnum` mixes in `str` but is still an Enum, so `str()` gives
    "DeliveryMethodEnum.DELIVERY" and a comparison against "delivery" fails
    silently. It did, and every delivery order reached the register as a pickup.
    """
    order = _order(delivery_method=DeliveryMethodEnum.DELIVERY)
    await pos_order_service.attach_online_order(None, order, BRANCH)
    assert order.order_type == OrderTypeEnum.DELIVERY.value


@pytest.mark.asyncio
async def test_a_pickup_order_is_labelled_a_pickup(attach):
    order = _order(delivery_method=DeliveryMethodEnum.PICKUP)
    await pos_order_service.attach_online_order(None, order, BRANCH)
    assert order.order_type == OrderTypeEnum.PICKUP.value


@pytest.mark.asyncio
async def test_the_counter_gets_somebody_to_call(attach):
    """A guest's name and number exist only in the shipping snapshot."""
    order = _order()
    await pos_order_service.attach_online_order(None, order, BRANCH)
    assert order.customer_name == "Hussain Abbasi"
    assert order.customer_phone == "+971501234567"


@pytest.mark.asyncio
async def test_the_storefront_order_number_is_kept(attach):
    """
    It is on the customer's confirmation email and it is what they will say on
    the phone. The check number is allocated alongside it, not instead of it.
    """
    order = _order()
    await pos_order_service.attach_online_order(None, order, BRANCH)
    assert order.order_number == "MM-20260804-001"
    assert order.check_number == 7


@pytest.mark.asyncio
async def test_attaching_twice_does_not_burn_a_second_check_number(attach):
    """
    Re-running must be free. A second check number would leave a gap in the
    counter's sequence and make "order 12" ambiguous for the rest of the day.
    """
    order = _order()
    await pos_order_service.attach_online_order(None, order, BRANCH)
    await pos_order_service.attach_online_order(None, order, BRANCH)
    assert attach["checks"] == 1
    assert order.check_number == 7
