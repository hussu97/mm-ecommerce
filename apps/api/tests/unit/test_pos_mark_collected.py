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
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import pos_orders
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.models.order import OrderStatusEnum
from app.models.pos_order import OrderItemStatusEnum, PosOrderStatusEnum
from app.services.orders import order_fees
from app.services.pos import pos_order_service

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
async def test_an_order_that_cannot_be_collected_is_refused(
    monkeypatch, wiring, status
):
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


# ── closing the counter, not just recording the collection ───────────────────
#
# The bug this heals: collecting used to advance only the delivery status, so
# the register check stayed `pos_status=active` with a null `closed_at` for
# ever and the admin "Counter" column read ACTIVE against a collected order.
# `mark_collected` now closes the check too — the real `close_order`, with its
# heavy collaborators stubbed the way `test_counter_auto_collect` stubs them so
# the settlement it performs (pos_status, closed_at, items, table) stays visible.


def _settleable_order(status: OrderStatusEnum, *, pos_status: str) -> SimpleNamespace:
    """A pickup website check `close_order` can actually settle."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260805-008",
        status=status,
        pos_status=pos_status,
        delivery_method="pickup",
        source="online",
        # A real prepaid website order carries NO `OrderPayment` rows — its card
        # was settled at the gateway — so `balance_due` reads as the whole total,
        # not zero. `close_order` must still settle it; the balance guard is for
        # cashier checks alone. A hardcoded `0` here would hide exactly that.
        balance_due=Decimal("120.00"),
        items=[SimpleNamespace(status=OrderItemStatusEnum.ACTIVE.value)],
        table_id=None,
        closer_id=None,
        closed_at=None,
        user_id=None,
    )


@pytest.fixture
def close_wiring(monkeypatch):
    """Stub `close_order`'s heavy collaborators; let it really settle the check."""
    order_holder: dict[str, SimpleNamespace] = {}
    release_table = AsyncMock()

    async def _get_order(db, order_id):
        return order_holder["order"]

    monkeypatch.setattr(pos_order_service, "get_order", _get_order)
    monkeypatch.setattr(
        pos_order_service.order_lifecycle, "transition", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(pos_order_service, "_release_table", release_table)
    monkeypatch.setattr(
        pos_order_service.inventory_service, "deplete_for_order", AsyncMock()
    )
    monkeypatch.setattr(order_fees, "stamp", AsyncMock())
    return order_holder, release_table


async def test_collecting_closes_the_counter_and_records_delivery(
    monkeypatch, wiring, close_wiring
):
    """A pickup order still open on the register (`pos_status=active`) is settled
    — closed, dated, items closed, table released — and then marked delivered."""
    update_status, notify = wiring
    order_holder, release_table = close_wiring
    order = _settleable_order(OrderStatusEnum.PACKED, pos_status="active")
    order_holder["order"] = order
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.mark_collected(
        order.id, db=AsyncMock(), user=_user("pos.register.access")
    )

    # The counter is now closed, not ACTIVE.
    assert order.pos_status == PosOrderStatusEnum.CLOSED.value
    assert order.closed_at is not None
    assert order.items[0].status == OrderItemStatusEnum.CLOSED.value
    release_table.assert_awaited_once()
    # And the collection was still recorded as delivered.
    update_status.assert_awaited_once()
    assert update_status.await_args.args[2] == OrderStatusEnum.DELIVERED
    notify.assert_awaited_once()


async def test_a_delivered_but_open_order_self_heals_to_closed(
    monkeypatch, wiring, close_wiring
):
    """The stuck case: a pickup order marked `delivered` on a laptop but whose
    till check was never closed. Pressing collect settles the counter, and the
    delivered fast-path does not short-circuit past that."""
    update_status, notify = wiring
    order_holder, release_table = close_wiring
    order = _settleable_order(OrderStatusEnum.DELIVERED, pos_status="active")
    order_holder["order"] = order
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    await pos_orders.mark_collected(
        order.id, db=AsyncMock(), user=_user("pos.register.access")
    )

    assert order.pos_status == PosOrderStatusEnum.CLOSED.value
    assert order.closed_at is not None
    release_table.assert_awaited_once()
    # Already delivered — no second transition, no duplicate email.
    update_status.assert_not_awaited()
    notify.assert_not_awaited()


async def test_re_pressing_a_settled_collection_is_idempotent(
    monkeypatch, wiring, close_wiring
):
    """Delivered *and* already closed: the counter-close is skipped (the check is
    no longer open) and the fast-path returns without emailing again."""
    update_status, notify = wiring
    order_holder, release_table = close_wiring
    order = _settleable_order(
        OrderStatusEnum.DELIVERED, pos_status=PosOrderStatusEnum.CLOSED.value
    )
    order_holder["order"] = order
    monkeypatch.setattr(pos_orders, "_load", AsyncMock(return_value=order))

    result = await pos_orders.mark_collected(
        order.id, db=AsyncMock(), user=_user("pos.register.access")
    )

    assert result is order
    assert order.pos_status == PosOrderStatusEnum.CLOSED.value
    release_table.assert_not_awaited()
    update_status.assert_not_awaited()
    notify.assert_not_awaited()


async def test_a_cashier_check_with_a_balance_still_cannot_close(close_wiring):
    """The balance guard survives for the counter's own sales. Lifting it for
    prepaid online orders (which never carry `OrderPayment` rows) must not lift
    it for a `cashier` check that genuinely still owes money at the till."""
    order_holder, _ = close_wiring
    order = _settleable_order(OrderStatusEnum.CONFIRMED, pos_status="active")
    order.source = "cashier"
    order.balance_due = Decimal("40.00")
    order_holder["order"] = order

    with pytest.raises(ConflictError, match="outstanding"):
        await pos_order_service.close_order(
            db=AsyncMock(), order=order, user=_user("pos.register.access")
        )
