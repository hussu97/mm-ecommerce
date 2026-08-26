"""
Cancelling an order from the counter — the red button beside Packed.

The counter may now cancel an **aggregator** order (declines the Foodics order
so the marketplace stops the rider) *and* a **website** order, pickup and
delivery alike (refunds the customer's card and cancels any booked MM courier).
Both run the full `cancelled` machinery through `order_service.update_status`;
the endpoint only decides who is allowed to press the button.

A **cashier** counter check is not cancelled here — it is voided through the
void flow, which is what keeps the till reconciled — so it stays rejected.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import pos_orders
from app.core.exceptions import ConflictError, ForbiddenError
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
    source: str,
    *,
    status: OrderStatusEnum = OrderStatusEnum.PACKED,
    delivery_method: str = "pickup",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260826-011",
        status=status,
        pos_status="active",
        source=source,
        delivery_method=delivery_method,
        user_id=None,
        items=[],
    )


@pytest.fixture
def wiring(monkeypatch):
    """The endpoint's collaborators, replaced so the test is about the endpoint."""
    update_status = AsyncMock()
    monkeypatch.setattr(pos_orders.order_service, "update_status", update_status)
    monkeypatch.setattr(pos_orders, "_serialise", lambda order: order)
    return update_status


async def test_a_website_pickup_order_can_be_cancelled(monkeypatch, wiring):
    """The new policy: the counter may cancel a website pickup order. Courier
    cancel is a natural no-op inside the transition for a pickup order."""
    update_status = wiring
    order = _order("online", delivery_method="pickup")
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.cancel_order(order.id, db=object(), user=_user("pos.orders.void"))

    update_status.assert_awaited_once()
    args = update_status.await_args.args
    assert args[1] == "MM-20260826-011"
    assert args[2] == OrderStatusEnum.CANCELLED


async def test_a_website_delivery_order_can_be_cancelled_even_from_packed(
    monkeypatch, wiring
):
    """Delivery too, and from `packed` (ready) — `update_status` widens the start
    state via `ONLINE_CANCELLABLE_FROM`; the refund and courier cancel fire
    inside `transition`."""
    update_status = wiring
    order = _order("online", status=OrderStatusEnum.PACKED, delivery_method="delivery")
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.cancel_order(order.id, db=object(), user=_user("pos.orders.void"))

    update_status.assert_awaited_once()
    assert update_status.await_args.args[2] == OrderStatusEnum.CANCELLED


async def test_an_aggregator_order_can_still_be_cancelled(monkeypatch, wiring):
    """The original path is untouched."""
    update_status = wiring
    order = _order("aggregator")
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.cancel_order(order.id, db=object(), user=_user("pos.orders.void"))

    update_status.assert_awaited_once()
    assert update_status.await_args.args[2] == OrderStatusEnum.CANCELLED


async def test_a_cashier_check_is_not_cancelled_here(monkeypatch, wiring):
    """A counter sale is voided through the void flow, not cancelled — voiding is
    what keeps the till reconciled. So the register cancel refuses it."""
    update_status = wiring
    order = _order("cashier")
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    with pytest.raises(ConflictError):
        await pos_orders.cancel_order(
            order.id, db=object(), user=_user("pos.orders.void")
        )

    update_status.assert_not_awaited()


async def test_cancelling_twice_is_not_an_error(monkeypatch, wiring):
    """Idempotent on an already-cancelled order, like `accept` and `packed`."""
    update_status = wiring
    order = _order("online", status=OrderStatusEnum.CANCELLED)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    result = await pos_orders.cancel_order(
        order.id, db=object(), user=_user("pos.orders.void")
    )

    assert result is order
    update_status.assert_not_awaited()


async def test_it_takes_the_void_permission():
    """`pos.orders.void` — cancelling from the counter is a void-level action."""
    dep = inspect.signature(pos_orders.cancel_order).parameters["user"].default
    assert dep.dependency.permission == "pos.orders.void"

    with pytest.raises(ForbiddenError):
        await dep.dependency(_user("pos.register.access"))
