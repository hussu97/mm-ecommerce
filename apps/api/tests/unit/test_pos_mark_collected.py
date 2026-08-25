"""
Saying "the customer took it" for a store-pickup order, from the counter.

A store-pickup order has no driver and no courier telemetry — nothing reports
back that the customer collected it. The only way to record it used to be an
admin on a laptop marking the whole order delivered; the shop that actually
handed the box over had no button. `POST /pos/orders/{id}/collected` is that
button: the pickup counterpart of `handed-over`.

`delivered` is the stored status; the storefront renders it as "Collected"
because the order is `pickup`. A delivery order is refused — its hand-over is
`handed-over` (→ `out_for_delivery`), driven by the courier.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import pos_orders
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.models.order import OrderStatusEnum

pytestmark = pytest.mark.asyncio


def _user(*permissions: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="cashier@meltingmoments.test",
        is_admin=False,
        can=lambda permission: permission in permissions,
    )


def _order(
    status: OrderStatusEnum, *, delivery_method: str = "pickup"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260805-008",
        status=status,
        pos_status="packed",
        delivery_method=delivery_method,
        user_id=None,
        items=[],
    )


@pytest.fixture
def wiring(monkeypatch):
    """The endpoint's collaborators, replaced so the test is about the endpoint."""
    update_status = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(pos_orders.order_service, "update_status", update_status)
    monkeypatch.setattr(pos_orders.email_service, "notify_order", notify)
    monkeypatch.setattr(pos_orders, "_serialise", lambda order: order)
    return update_status, notify


async def test_collecting_delegates_rather_than_reimplementing(monkeypatch, wiring):
    """The transition rules and the email live in `order_service`/`email_service`;
    the endpoint only names the status."""
    update_status, notify = wiring
    order = _order(OrderStatusEnum.PACKED)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.mark_collected(
        order.id, db=object(), user=_user("pos.register.access")
    )

    update_status.assert_awaited_once()
    args = update_status.await_args.args
    assert args[1] == "MM-20260805-008"
    assert args[2] == OrderStatusEnum.DELIVERED
    # The "Collected" email is the message the customer is waiting on.
    notify.assert_awaited_once()


async def test_a_delivery_order_is_not_collected_at_the_counter(monkeypatch, wiring):
    """Collection is a pickup fact. A delivery order's hand-over is `handed-over`
    (→ out_for_delivery); letting the counter mark it delivered would skip the
    courier and tell the customer it arrived when it had not left."""
    update_status, notify = wiring
    order = _order(OrderStatusEnum.PACKED, delivery_method="delivery")
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    with pytest.raises(BadRequestError):
        await pos_orders.mark_collected(
            order.id, db=object(), user=_user("pos.register.access")
        )

    update_status.assert_not_awaited()
    notify.assert_not_awaited()


async def test_collecting_twice_is_not_an_error(monkeypatch, wiring):
    """Two people will press it; the slower one should not get an error, nor a
    duplicate "Collected" email."""
    update_status, notify = wiring
    order = _order(OrderStatusEnum.DELIVERED)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    result = await pos_orders.mark_collected(
        order.id, db=object(), user=_user("pos.register.access")
    )

    assert result is order
    update_status.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.CREATED,  # not accepted yet
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
    ],
)
async def test_an_order_that_cannot_be_collected_is_refused(monkeypatch, wiring, status):
    """The same transition table the console is held to — collection can only
    follow the order actually being ready."""
    update_status, notify = wiring
    order = _order(status)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    with pytest.raises(ConflictError) as raised:
        await pos_orders.mark_collected(
            order.id, db=object(), user=_user("pos.register.access")
        )

    assert raised.value.status_code == 409
    assert status.value in raised.value.detail
    update_status.assert_not_awaited()
    notify.assert_not_awaited()


async def test_it_takes_the_same_permission_as_the_register():
    """`pos.register.access` — whoever may take the order may say it was collected."""
    dep = inspect.signature(pos_orders.mark_collected).parameters["user"].default
    assert dep.dependency.permission == "pos.register.access"

    with pytest.raises(ForbiddenError):
        await dep.dependency(_user("orders.read"))
