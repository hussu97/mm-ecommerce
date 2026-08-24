"""
Saying "the box is finished" from the counter.

`packed` is the event the whole delivery chain hangs off: it assigns the order
to a batch, books a courier and sends the customer the email saying their cake
is on its way. The only way to say it used to be `PUT
/orders/{order_number}/status`, which is gated on `get_admin_user` — and a
cashier signed in with a branch PIN is not an admin.

So a website order accepted on the register sat at `confirmed` until somebody
opened the admin console on a laptop. If nobody did, no courier was ever booked
and the customer never heard again.
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
        # Read when the packed transition is attributed to this cashier in
        # `order_status_events`. A stand-in has to carry every field the code
        # touches, or the omission surfaces as an `AttributeError` in whichever
        # test happens to run first rather than as a missing column.
        email="cashier@meltingmoments.test",
        is_admin=False,
        can=lambda permission: permission in permissions,
    )


def _order(status: OrderStatusEnum) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260805-008",
        status=status,
        pos_status="active",
        user_id=None,
        amount_paid=0,
        balance_due=0,
        shipping_address_snapshot={"address_line_1": "Villa 1"},
        items=[],
    )


@pytest.fixture
def wiring(monkeypatch):
    """
    The endpoint's collaborators, replaced so the test is about the endpoint.

    `_serialise` too: it builds a full response model out of an ORM row, and
    what is under test is which calls happen and in what order — not the
    payload, which every other POS endpoint already exercises.
    """
    update_status = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(pos_orders.order_service, "update_status", update_status)
    monkeypatch.setattr(pos_orders.email_service, "notify_order", notify)
    monkeypatch.setattr(pos_orders, "_serialise", lambda order: order)
    return update_status, notify


async def test_packing_delegates_rather_than_reimplementing(monkeypatch, wiring):
    """
    The transition rules, the batch assignment and the email all live in
    `order_service.update_status` and `email_service`. A second implementation
    here is how the register and the console would come to disagree about what
    "packed" means.
    """
    update_status, notify = wiring
    order = _order(OrderStatusEnum.CONFIRMED)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.mark_packed(
        order.id, db=object(), user=_user("pos.register.access")
    )

    update_status.assert_awaited_once()
    args = update_status.await_args.args
    assert args[1] == "MM-20260805-008"
    assert args[2] == OrderStatusEnum.PACKED
    # And the customer is told. This is the email the whole feature exists for.
    notify.assert_awaited_once()


async def test_packing_twice_is_not_an_error(monkeypatch, wiring):
    """Two people will press it on a busy counter, and the slower one should
    not get an error for being slower."""
    update_status, notify = wiring
    order = _order(OrderStatusEnum.PACKED)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    result = await pos_orders.mark_packed(
        order.id, db=object(), user=_user("pos.register.access")
    )

    assert result is order
    update_status.assert_not_awaited()
    # Silence matters as much as the non-error: re-sending "on its way" every
    # time somebody taps a button would be worse than the missing email was.
    notify.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.CREATED,  # not paid for yet
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DELIVERED,
    ],
)
async def test_an_order_that_cannot_be_packed_is_refused(monkeypatch, wiring, status):
    """
    The same transition table the console is held to. A cancelled order is not
    a thing anybody can finish packing, and letting the counter say so would
    book a courier for a cake nobody is making.
    """
    update_status, notify = wiring
    order = _order(status)
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    with pytest.raises(ConflictError) as raised:
        await pos_orders.mark_packed(
            order.id, db=object(), user=_user("pos.register.access")
        )

    assert raised.value.status_code == 409
    assert status.value in raised.value.detail
    update_status.assert_not_awaited()
    notify.assert_not_awaited()


async def test_it_takes_the_same_permission_as_the_register():
    """
    `pos.register.access` — whoever may take the order may say it is finished.
    A separate permission would mean a shop that had not thought to grant it
    silently went back to needing a laptop.

    The check lives in the route's signature now (`Depends(require(...))`), so
    the test reads it off the declared dependency and exercises the dependency
    itself — a direct call to the handler no longer passes through the gate.
    """
    dep = inspect.signature(pos_orders.mark_packed).parameters["user"].default
    assert dep.dependency.permission == "pos.register.access"

    with pytest.raises(ForbiddenError):
        await dep.dependency(_user("orders.read"))


@pytest.mark.asyncio(loop_scope="function")
async def test_the_register_can_see_which_lifecycle_an_order_is_in():
    """
    `status` is on the payload because the register cannot offer "mark packed"
    without knowing whether that already happened — and `pos_status` does not
    answer it. A website order can be an open check (`pos_status = active`) and
    still be sitting at `confirmed` with no courier booked, which is exactly the
    state this whole change exists to get orders out of.
    """
    from app.schemas.pos_order import PosOrderResponse

    assert "status" in PosOrderResponse.model_fields
