"""
`order_lifecycle.transition` — the one door, and what walking through it does.

The consequence tests pin the property the refactor exists for: what a status
makes happen is keyed off the transition, not off who asked for it. The refund
on `undelivered`, the register void and restock on `cancelled`, the publish on
`confirmed` — each fires identically for the console, a webhook, the checkout
and the till, because they all run this function.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

import app.services.order_service  # noqa: F401 — registers the warn listener's neighbours
from app.core.exceptions import BadRequestError
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum, PosOrderStatusEnum
from app.services import order_lifecycle


def _order(status: OrderStatusEnum, **overrides) -> Order:
    """A real `Order`, so the status listeners fire, with an empty history."""
    order = Order(
        id=uuid.uuid4(),
        status=status,
        order_number="MM-LC-1",
        source=OrderSourceEnum.ONLINE.value,
        pos_status=None,
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    inspect(order).info.pop("pending_status_events", None)
    return order


class _Db:
    """Records the statements `_consequences` executes (the restock updates)."""

    def __init__(self):
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))


@pytest.fixture
def quiet_consequences(monkeypatch):
    """
    Stub the services a consequence reaches for, and record the calls.

    Patched on their own modules because `_consequences` imports them lazily —
    the same seam the noon Send tests use.
    """
    from app.services import (
        batching_service,
        courier_service,
        lalamove_service,
        order_service,
        payment_service,
    )

    calls: dict[str, list] = {
        "publish": [],
        "dispatch": [],
        "batch_cancel": [],
        "courier_cancel": [],
        "refund": [],
    }

    async def publish(db, order, branch=None):
        calls["publish"].append(order)

    async def dispatch(db, order):
        calls["dispatch"].append(order)

    async def get_delivery(db, order_id):
        return None

    async def cancel_assignment(db, delivery):
        calls["batch_cancel"].append(delivery)

    async def courier_cancel(db, order):
        calls["courier_cancel"].append(order)

    async def refund(db, order):
        calls["refund"].append(order)
        return Decimal("0")

    monkeypatch.setattr(order_service, "publish_to_register", publish)
    monkeypatch.setattr(batching_service, "assign_or_dispatch", dispatch)
    monkeypatch.setattr(batching_service, "cancel_assignment", cancel_assignment)
    monkeypatch.setattr(lalamove_service, "get_delivery", get_delivery)
    monkeypatch.setattr(courier_service, "cancel", courier_cancel)
    monkeypatch.setattr(payment_service, "refund_order", refund)
    return calls


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_illegal_move_raises_for_interactive_callers(quiet_consequences):
    order = _order(OrderStatusEnum.CREATED)
    with pytest.raises(BadRequestError):
        await order_lifecycle.transition(_Db(), order, OrderStatusEnum.PACKED)
    assert order.status == OrderStatusEnum.CREATED


@pytest.mark.asyncio
async def test_an_illegal_move_is_declined_quietly_for_webhooks(quiet_consequences):
    """`on_invalid="skip"` — stale courier news is an event, not a request."""
    order = _order(OrderStatusEnum.REFUNDED)
    moved = await order_lifecycle.transition(
        _Db(), order, OrderStatusEnum.DELIVERED, on_invalid="skip"
    )
    assert moved is False
    assert order.status == OrderStatusEnum.REFUNDED


@pytest.mark.asyncio
async def test_arriving_where_you_already_are_is_a_quiet_no_op(quiet_consequences):
    """And crucially, consequences do not fire twice."""
    order = _order(OrderStatusEnum.CONFIRMED)
    moved = await order_lifecycle.transition(
        _Db(), order, OrderStatusEnum.CONFIRMED, on_invalid="skip"
    )
    assert moved is False
    assert quiet_consequences["publish"] == []


@pytest.mark.asyncio
async def test_extra_from_widens_where_a_move_may_start(quiet_consequences):
    """
    A dashboard refund can land on an order that never confirmed. The gateway's
    ledger outranks the map on where the money went — but only the caller who
    holds that fact gets the widening, and only for the start of the move.
    """
    order = _order(OrderStatusEnum.CREATED)
    moved = await order_lifecycle.transition(
        _Db(),
        order,
        OrderStatusEnum.REFUNDED,
        extra_from={OrderStatusEnum.CREATED},
        on_invalid="skip",
    )
    assert moved is True
    assert order.status == OrderStatusEnum.REFUNDED


# ── consequences ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirming_publishes_to_the_register(quiet_consequences):
    order = _order(OrderStatusEnum.CREATED)
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CONFIRMED)
    assert quiet_consequences["publish"] == [order]


@pytest.mark.asyncio
async def test_packing_asks_for_a_van(quiet_consequences):
    order = _order(OrderStatusEnum.CONFIRMED)
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.PACKED)
    assert quiet_consequences["dispatch"] == [order]


@pytest.mark.asyncio
async def test_undelivered_refunds_whoever_reports_it(quiet_consequences):
    """The P0 gap: this used to fire only when an admin clicked."""
    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY)
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.UNDELIVERED)
    assert quiet_consequences["refund"] == [order]


@pytest.mark.asyncio
async def test_cancelling_a_website_order_releases_its_stock(quiet_consequences):
    """Checkout claimed the stock at creation; cancellation hands it back."""
    db = _Db()
    order = _order(
        OrderStatusEnum.CONFIRMED,
        items=[OrderItem(product_id=uuid.uuid4(), quantity=2)],
    )
    await order_lifecycle.transition(db, order, OrderStatusEnum.CANCELLED)
    assert len(db.executed) == 1
    assert quiet_consequences["refund"] == [order]
    assert quiet_consequences["courier_cancel"] == [order]


@pytest.mark.asyncio
async def test_cancelling_a_counter_sale_restocks_nothing(quiet_consequences):
    """
    A counter sale never claimed `stock_quantity` — it depletes recipe
    ingredients at close instead. Restocking it here would invent stock, which
    is what cancelling one from the console used to do.
    """
    db = _Db()
    order = _order(
        OrderStatusEnum.CONFIRMED,
        source=OrderSourceEnum.CASHIER.value,
        items=[OrderItem(product_id=uuid.uuid4(), quantity=2)],
    )
    await order_lifecycle.transition(db, order, OrderStatusEnum.CANCELLED)
    assert db.executed == []


@pytest.mark.asyncio
async def test_cancelling_voids_a_check_still_open_on_the_register(
    quiet_consequences,
):
    order = _order(
        OrderStatusEnum.CONFIRMED,
        pos_status=PosOrderStatusEnum.ACTIVE.value,
        items=[],
    )
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CANCELLED)
    assert order.pos_status == PosOrderStatusEnum.VOID.value


@pytest.mark.asyncio
async def test_cancelling_leaves_a_settled_check_alone(quiet_consequences):
    """A late cancellation must not stamp `void` over a paid, closed check."""
    order = _order(
        OrderStatusEnum.CONFIRMED,
        pos_status=PosOrderStatusEnum.CLOSED.value,
        items=[],
    )
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CANCELLED)
    assert order.pos_status == PosOrderStatusEnum.CLOSED.value


# ── correcting an ending, from the console only ──────────────────────────────
#
# `undelivered` and `cancelled` are endings and the map keeps them that way.
# What changed is that a *person* may take an order back out of one and record
# that it did arrive — a driver who reported a failed handover and then found
# the customer, a cancellation keyed against the wrong order number. The route
# is `extra_from`, and `order_service.update_status` is the only caller that
# passes it.


@pytest.mark.parametrize(
    "ending", [OrderStatusEnum.UNDELIVERED, OrderStatusEnum.CANCELLED]
)
def test_the_shared_map_still_refuses_the_way_back(ending):
    """
    The property that keeps this admin-only. `VALID_TRANSITIONS` is what the
    courier webhooks and the register read, and a late `delivered` push from a
    courier must not resurrect an order the shop has already written off.
    """
    assert not order_lifecycle.can_transition(ending, OrderStatusEnum.DELIVERED)
    assert ending in order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", [OrderStatusEnum.UNDELIVERED, OrderStatusEnum.CANCELLED]
)
async def test_a_webhook_cannot_walk_an_ending_back(quiet_consequences, ending):
    order = _order(ending, items=[])
    moved = await order_lifecycle.transition(
        _Db(), order, OrderStatusEnum.DELIVERED, on_invalid="skip"
    )
    assert moved is False
    assert order.status == ending


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ending", [OrderStatusEnum.UNDELIVERED, OrderStatusEnum.CANCELLED]
)
async def test_the_console_may_correct_an_ending_to_delivered(
    quiet_consequences, ending
):
    order = _order(ending, items=[])
    moved = await order_lifecycle.transition(
        _Db(),
        order,
        OrderStatusEnum.DELIVERED,
        extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
    )
    assert moved is True
    assert order.status == OrderStatusEnum.DELIVERED


@pytest.mark.asyncio
async def test_correcting_a_cancellation_takes_the_stock_back_off_the_shelf():
    """
    The mirror of the restock, and the reason `_consequences` was given the
    previous status at all. The cancellation put two back; the box did leave,
    so two have to come off again. Without this, every corrected cancellation
    overstates stock by its own contents — permanently, and with no error.
    """
    db = _Db()
    order = _order(
        OrderStatusEnum.CANCELLED,
        items=[OrderItem(product_id=uuid.uuid4(), quantity=2)],
    )
    await order_lifecycle.transition(
        db,
        order,
        OrderStatusEnum.DELIVERED,
        extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
    )
    assert len(db.executed) == 1
    values = db.executed[0].compile().params
    assert values["stock_quantity_1"] == -2, "stock was handed back a second time"


@pytest.mark.asyncio
async def test_correcting_an_undelivered_order_moves_no_stock():
    """
    Only a cancellation ever gave stock back, so only a cancellation owes it.
    An undelivered order never left the ledger.
    """
    db = _Db()
    order = _order(
        OrderStatusEnum.UNDELIVERED,
        items=[OrderItem(product_id=uuid.uuid4(), quantity=2)],
    )
    await order_lifecycle.transition(
        db,
        order,
        OrderStatusEnum.DELIVERED,
        extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
    )
    assert db.executed == []


@pytest.mark.asyncio
async def test_correcting_a_refunded_ending_says_so_in_the_log(caplog):
    """
    The refund is not reversed and cannot be — the money is at a bank, not in a
    column. An order reading `delivered` with a non-zero `refunded_amount` is a
    real loss, so it has to be loud somewhere.
    """
    order = _order(
        OrderStatusEnum.UNDELIVERED, items=[], refunded_amount=Decimal("120.00")
    )
    with caplog.at_level("WARNING", logger="app.services.order_lifecycle"):
        await order_lifecycle.transition(
            _Db(),
            order,
            OrderStatusEnum.DELIVERED,
            extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
        )
    assert "already" in caplog.text and "refunded" in caplog.text


@pytest.mark.asyncio
async def test_a_correction_does_not_reopen_a_voided_check():
    """
    Deliberate. A settled check reopened is a till that no longer reconciles,
    which is a worse record than a voided check beside a delivered order.
    """
    order = _order(
        OrderStatusEnum.CANCELLED, pos_status=PosOrderStatusEnum.VOID.value, items=[]
    )
    await order_lifecycle.transition(
        _Db(),
        order,
        OrderStatusEnum.DELIVERED,
        extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
    )
    assert order.pos_status == PosOrderStatusEnum.VOID.value
