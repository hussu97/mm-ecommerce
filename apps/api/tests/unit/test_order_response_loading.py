"""
An order handed to the mailer must carry its lines, whoever selected it.

`OrderResponse` has an `items` field, so validating an order whose collection is
still unloaded triggers a lazy load. Inside async SQLAlchemy that is not a slow
query — it is a `MissingGreenlet`, and `email_service.notify_order` catches
everything, so the email vanishes without even an `email_logs` row to show for
it.

That is not hypothetical. On 2026-08-05 both courier webhooks selected the order
with a bare `select(Order)`, and four customer emails were lost in one morning:
`out_for_delivery` and `delivered` for MM-20260805-007, `out_for_delivery` and
`undelivered` for -006. Every order status moved correctly; nobody was told.

The suite mocks the database, which is exactly why nothing caught it — a mock
answers a lazy load happily. So these tests use a **detached** instance instead.
Detached is the one state where SQLAlchemy refuses an unloaded attribute without
needing a live connection, and its `DetachedInstanceError` stands in for the
`MissingGreenlet` a real async session would raise.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.orm.exc import DetachedInstanceError

from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.schemas.order import OrderResponse
from app.services import order_service

NOW = datetime(2026, 8, 5, 8, 2, tzinfo=timezone.utc)


def _order(**overrides) -> Order:
    """
    An order with every column set, exactly as a `select(Order)` returns one.

    All of them, including the nullable ones: a detached instance treats any
    column it was not given as unloaded too, and the point of these tests is
    that `items` is the *only* thing missing.
    """
    fields: dict = {
        "id": uuid.uuid4(),
        "order_number": "MM-20260805-007",
        "user_id": None,
        "email": "customer@example.com",
        "delivery_method": DeliveryMethodEnum.DELIVERY,
        "subtotal": Decimal("3.50"),
        "total": Decimal("3.50"),
        "delivery_fee": Decimal("0"),
        "low_order_fee": Decimal("0"),
        "discount_amount": Decimal("0"),
        "vat_rate": Decimal("0.05"),
        "vat_amount": Decimal("0.17"),
        "total_excl_vat": Decimal("3.33"),
        # Every column, per the docstring — a detached instance treats anything
        # it was not given as unloaded, and the whole point of these tests is
        # that `items` is the only thing missing.
        "refunded_amount": Decimal("0"),
        "refunded_at": None,
        "status": OrderStatusEnum.DELIVERED,
        "promo_code_used": None,
        "shipping_address_snapshot": None,
        "payment_method": "stripe",
        "payment_provider": "stripe",
        "payment_id": "pi_test",
        "notes": None,
        "admin_notes": None,
        "source": "online",
        "aggregator_channel": None,
        "aggregator_delivery_fee": None,
        "aggregator_display_code": None,
        "external_reference": None,
        "locale": "en",
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return Order(**fields)


def _detached_order(**overrides) -> Order:
    """A persisted-looking order whose `items` were never loaded."""
    order = _order(**overrides)
    make_transient_to_detached(order)
    return order


def test_an_unloaded_collection_really_does_break_validation():
    """The bug itself, so the guard below is protecting against something real."""
    order = _detached_order()
    assert "items" in sa_inspect(order).unloaded

    with pytest.raises(Exception) as caught:
        OrderResponse.model_validate(order)
    # Pydantic wraps it, so the cause is what identifies the failure.
    assert "items" in str(caught.value)


async def test_to_response_loads_the_lines_the_caller_forgot(mock_db):
    """`to_response` refreshes rather than letting the validation lazy-load."""
    order = _detached_order()
    refreshed: list[tuple] = []

    async def refresh(instance, attribute_names=None):
        refreshed.append((instance, tuple(attribute_names or ())))
        # What a real refresh does: the collection is now present.
        instance.__dict__["items"] = []

    mock_db.refresh = refresh

    response = await order_service.to_response(mock_db, order)

    assert refreshed == [(order, ("items",))]
    assert response.order_number == "MM-20260805-007"
    assert response.items == []


async def test_a_transient_order_is_left_alone(mock_db):
    """The POS builds orders by hand; there is nothing to refresh against."""
    order = _order(
        order_number="MM-20260805-008",
        email="counter@example.com",
        delivery_method=DeliveryMethodEnum.PICKUP,
        status=OrderStatusEnum.CONFIRMED,
        source="cashier",
    )
    called = False

    async def refresh(instance, attribute_names=None):
        nonlocal called
        called = True

    mock_db.refresh = refresh

    await order_service.to_response(mock_db, order)

    assert called is False, "a transient order has no identity to refresh"


async def test_detached_access_is_the_stand_in_we_think_it_is():
    """Guards the premise of this whole file rather than the code under test."""
    order = _detached_order()
    with pytest.raises(DetachedInstanceError):
        _ = order.items
