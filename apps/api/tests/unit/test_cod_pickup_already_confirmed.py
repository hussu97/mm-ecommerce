"""
Placing a cash store-pickup order must not 400 on the pay button.

The storefront places the order (`POST /orders`) and then asks for a payment
session (`POST /payments/session`) as two requests. `create_order` confirms a
cash order, and confirmation lands a *pickup* at `arrived_at_pos` in the same
breath — a collection is due the instant it is confirmed
(`arrival_service.schedule` → `land`). So the second request's `create_session`
arrives with the order already one step past `confirmed`.

It used to read only `== CONFIRMED`, miss that, and try an illegal
`arrived_at_pos → confirmed`, which raised `BadRequestError` — the toast every
pickup order showed at checkout. `create_session` now recognises a
confirmed-or-beyond order as done: it re-publishes to the register (a no-op the
second time) and returns success.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.payments import payment_service


def _db() -> MagicMock:
    session = MagicMock()
    session.flush = AsyncMock()
    return session


def _order(status: OrderStatusEnum) -> SimpleNamespace:
    return SimpleNamespace(
        id="order-uuid",
        order_number="MM-20260918-001",
        email="c@example.com",
        total=Decimal("85.00"),
        status=status,
        delivery_method=DeliveryMethodEnum.PICKUP,
        payment_method=None,
        payment_provider=None,
        payment_id=None,
        payment_transactions=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [OrderStatusEnum.CONFIRMED, OrderStatusEnum.ARRIVED_AT_POS],
)
async def test_cod_pickup_already_confirmed_does_not_reconfirm(status):
    db = _db()
    order = _order(status)
    publish = AsyncMock()

    with (
        patch.object(payment_service, "_load_order", AsyncMock(return_value=order)),
        patch.object(payment_service, "_assert_may_act_on"),
        patch.object(payment_service.order_service, "publish_to_register", publish),
        patch.object(
            payment_service.order_lifecycle, "transition", AsyncMock()
        ) as transition,
    ):
        result = await payment_service.create_session(
            db, order.order_number, method="cod"
        )

    assert result["confirmed"] is True
    assert result["session_id"] is None
    # The order was already confirmed by `create_order`; nothing tries to move
    # its status again — which is what used to raise from `arrived_at_pos`.
    transition.assert_not_awaited()
    # The register is told again, harmlessly.
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_cod_pickup_still_created_confirms_once():
    """A cash order that reached the pay button still at `created` — no
    `create_order` confirm — is confirmed here, exactly as before."""
    db = _db()
    order = _order(OrderStatusEnum.CREATED)

    def _confirm(_db, o, new_status):
        o.status = new_status

    with (
        patch.object(payment_service, "_load_order", AsyncMock(return_value=order)),
        patch.object(payment_service, "_assert_may_act_on"),
        patch.object(
            payment_service.order_service,
            "to_response",
            AsyncMock(return_value=SimpleNamespace()),
        ),
        patch.object(
            payment_service.email_service,
            "send_order_confirmation",
            AsyncMock(),
        ),
        patch.object(
            payment_service.email_service,
            "send_owner_order_notification",
            AsyncMock(),
        ),
        patch.object(
            payment_service.order_lifecycle,
            "transition",
            AsyncMock(side_effect=_confirm),
        ) as transition,
    ):
        result = await payment_service.create_session(
            db, order.order_number, method="cod"
        )

    assert result["confirmed"] is True
    transition.assert_awaited_once()
    assert transition.await_args.args[2] == OrderStatusEnum.CONFIRMED
