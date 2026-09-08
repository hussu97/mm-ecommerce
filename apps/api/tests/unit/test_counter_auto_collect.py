"""
Closing a counter check collects it.

A counter sale is collected the instant it is paid for — the customer is
standing at the till with the box. So `close_order` takes a cashier check all
the way to `delivered` (rendered "Collected", since every counter order is
`pickup`), not just `confirmed`. Before this, a counter sale sat at `confirmed`
for ever and never showed up in any fulfilment or "collected" view.

An online order closed at the counter as a hand-over must NOT be swept to
delivered here — its delivery lifecycle is driven by the courier/admin — so the
extra step is gated on `source == cashier`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.order import OrderStatusEnum
from app.services.orders import order_fees
from app.services.pos import pos_order_service

pytestmark = pytest.mark.asyncio


def _order(source: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="POS-K001-20260825-0007",
        pos_status="active",
        balance_due=Decimal("0"),
        source=source,
        items=[],
        table_id=None,
        closer_id=None,
        closed_at=None,
    )


@pytest.fixture
def wiring(monkeypatch):
    """Replace close_order's heavy collaborators; keep the status moves visible."""
    order_holder: dict[str, SimpleNamespace] = {}
    transition = AsyncMock()

    async def _get_order(db, order_id):
        return order_holder["order"]

    monkeypatch.setattr(pos_order_service, "get_order", _get_order)
    monkeypatch.setattr(pos_order_service.order_lifecycle, "transition", transition)
    monkeypatch.setattr(pos_order_service, "_release_table", AsyncMock())
    monkeypatch.setattr(
        pos_order_service.inventory_service, "deplete_for_order", AsyncMock()
    )
    monkeypatch.setattr(order_fees, "stamp", AsyncMock())
    return order_holder, transition


def _statuses(transition: AsyncMock) -> list:
    """The destination status of every transition() call, in order."""
    return [call.args[2] for call in transition.await_args_list]


async def test_a_counter_sale_is_confirmed_then_collected(wiring):
    order_holder, transition = wiring
    order_holder["order"] = _order("cashier")

    await pos_order_service.close_order(
        AsyncMock(),
        order=order_holder["order"],
        user=SimpleNamespace(id=uuid.uuid4(), email="c@mm.test"),
    )

    # Confirmed (the close) then delivered (the collection), in that order.
    assert _statuses(transition) == [
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.DELIVERED,
    ]


async def test_close_time_is_stamped_before_the_status_transitions(wiring):
    """`closed_at` must be set the instant `pos_status` becomes `closed`, before
    any transition runs.

    The CONFIRMED transition triggers the inventory acceptance, whose first
    SELECT autoflushes the order. `ck_orders_closed_has_closed_at` (migration
    141) rejects a `closed` row with a null `closed_at`, so if the close time
    were stamped only after the transitions, that autoflush would write a
    `(closed, null)` row, the constraint would reject it, and the whole close
    would roll back — which stranded every counter sale at `created` with no
    receipt. Assert the two move together so the ordering cannot regress.
    """
    order_holder, transition = wiring
    order = _order("cashier")
    order_holder["order"] = order

    observed: list[tuple[str, object]] = []

    async def _capture(db, ord_, status, **kwargs):
        # What the DB would see if an autoflush fired at this transition.
        observed.append((ord_.pos_status, ord_.closed_at))

    transition.side_effect = _capture

    await pos_order_service.close_order(
        AsyncMock(),
        order=order,
        user=SimpleNamespace(id=uuid.uuid4(), email="c@mm.test"),
    )

    assert observed, "close_order never transitioned the order"
    for pos_status, closed_at in observed:
        assert pos_status == "closed"
        assert closed_at is not None, (
            "closed_at was null while pos_status was 'closed' at a transition "
            "point — an autoflush here trips ck_orders_closed_has_closed_at"
        )


async def test_an_online_hand_over_is_not_swept_to_delivered(wiring):
    """A website order closed at the counter is only confirmed — the courier and
    admin own its delivery, and jumping it to delivered here would tell the
    customer it arrived before a driver ever picked it up."""
    order_holder, transition = wiring
    order_holder["order"] = _order("online")

    await pos_order_service.close_order(
        AsyncMock(),
        order=order_holder["order"],
        user=SimpleNamespace(id=uuid.uuid4(), email="c@mm.test"),
    )

    assert _statuses(transition) == [OrderStatusEnum.CONFIRMED]
