"""
`POST /orders` answers a repeat with 200 and a first create with 201 (F-WEB-5).

The handler is exercised directly rather than over HTTP so the status decision
can be read off the injected `Response` without building a whole `OrderResponse`
to satisfy the wire serializer — the service functions it calls are stubbed. The
end-to-end "no duplicate" guarantee lives in the integration idempotency test.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from fastapi import Response, status

from app.api.v1 import orders
from app.models.order import DeliveryMethodEnum
from app.schemas.order import OrderCreate


def _data(**over) -> OrderCreate:
    base = dict(
        email="jane@example.com",
        delivery_method=DeliveryMethodEnum.PICKUP,
        payment_method="cod",
        # A pickup order now carries the collecting customer's contact.
        pickup_contact={
            "first_name": "Jane",
            "last_name": "Doe",
            "phone": "+971501234567",
        },
    )
    return OrderCreate(**{**base, **over})


def _created_response() -> Response:
    """A response pre-stamped 201 the way FastAPI applies the route's
    `status_code=201` default before the handler runs, so the test can tell an
    untouched default from a deliberate 200 override."""
    response = Response()
    response.status_code = status.HTTP_201_CREATED
    return response


async def test_a_repeat_is_answered_200(monkeypatch):
    monkeypatch.setattr(
        orders.order_service,
        "get_by_client_request_id",
        AsyncMock(return_value=object()),  # an order already carries this key
    )
    created = AsyncMock(return_value=object())
    monkeypatch.setattr(orders.order_service, "create_order", created)

    response = _created_response()
    await orders.create_order(
        data=_data(client_request_id=uuid.uuid4()),
        response=response,
        db=AsyncMock(),
        current_user=None,
    )
    assert response.status_code == status.HTTP_200_OK
    created.assert_awaited_once()


async def test_a_first_create_leaves_the_201_default(monkeypatch):
    monkeypatch.setattr(
        orders.order_service,
        "get_by_client_request_id",
        AsyncMock(return_value=None),  # nothing carries this key yet
    )
    monkeypatch.setattr(
        orders.order_service, "create_order", AsyncMock(return_value=object())
    )

    response = _created_response()
    await orders.create_order(
        data=_data(client_request_id=uuid.uuid4()),
        response=response,
        db=AsyncMock(),
        current_user=None,
    )
    # Untouched — the handler overrode nothing, so the decorator's 201 stands.
    assert response.status_code == status.HTTP_201_CREATED


async def test_no_key_skips_the_idempotency_lookup(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(orders.order_service, "get_by_client_request_id", lookup)
    monkeypatch.setattr(
        orders.order_service, "create_order", AsyncMock(return_value=object())
    )

    await orders.create_order(
        data=_data(),  # no client_request_id
        response=Response(),
        db=AsyncMock(),
        current_user=None,
    )
    lookup.assert_not_awaited()


async def test_recovery_lookup_is_guest_reachable_and_empty_when_unknown(monkeypatch):
    """`GET /orders?client_request_id=` is answered for a guest (no
    `current_user`) — the storefront's timeout recovery may never have signed in
    — and an unknown key is an empty page, not a 401 or a 404."""
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(orders.order_service, "get_by_client_request_id", lookup)

    result = await orders.list_my_orders(
        page=1,
        per_page=20,
        client_request_id=uuid.uuid4(),
        email="jane@example.com",
        db=AsyncMock(),
        current_user=None,
    )
    assert result.items == []
    assert result.total == 0
    lookup.assert_awaited_once()
