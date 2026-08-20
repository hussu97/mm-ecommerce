"""
A van being booked has to reach the register, not just the delivery row.

MM-20260820-002 is why this file exists. A Sharjah order, paid at 05:29 and
sitting on a run that was due to leave at 08:00, was dispatched by hand from the
console at 05:31. Lalamove took it, a driver was assigned, and the order was
stamped `packed` — straight from `confirmed`, which `VALID_TRANSITIONS` allows.

`publish_to_register` hangs off `arrived_at_pos` and off nothing else, so the
kitchen was never told: no check number, no ticket on the printer, no alarm, and
nothing on the terminal to accept. A rider was on his way to collect a box of
six brownies that nobody had been asked to make. Nor could the arrival sweep
recover it afterwards — that sweep looks for orders still at `confirmed`, and
this one had already gone past.

The same skip sat on the batched path, where it was worse for being routine:
`_book_chunk` stamps every order on a run packed the moment the run is booked,
and the sweep that would have landed those orders runs immediately *after* the
batch dispatch in the same tick. Every batched order would have found its way to
`packed` without a register ever hearing about it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.order import OrderStatusEnum
from app.services import arrival_service, order_service


def _order(status: OrderStatusEnum) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260820-002",
        status=status,
        branch_id=uuid.uuid4(),
    )


@pytest.fixture
def watched(monkeypatch):
    """`land` and the packing transition, recorded instead of run."""
    calls: dict[str, list] = {"landed": [], "packed": []}

    async def land(db, order, *, because):
        calls["landed"].append(because)
        # What the real one does, and what the guard below then reads.
        order.status = OrderStatusEnum.ARRIVED_AT_POS
        return True

    async def update_status(db, order_number, status, *args, **kwargs):
        calls["packed"].append(status)

    monkeypatch.setattr(arrival_service, "land", land)
    monkeypatch.setattr(order_service, "update_status", update_status)
    return calls


async def test_a_booking_lands_the_order_before_it_packs_it(watched):
    """
    The order MM-20260820-002 was: booked while still `confirmed`.

    The shop is told first — with the courier reference already on the delivery
    row, so the ticket that prints carries it — and only then is the box called
    finished.
    """
    order = _order(OrderStatusEnum.CONFIRMED)

    assert await order_service.stamp_packed(
        None, order, note="lalamove booking accepted"
    )

    assert watched["landed"] == ["lalamove booking accepted"]
    assert watched["packed"] == [OrderStatusEnum.PACKED]


async def test_an_order_already_on_the_register_is_not_landed_twice(watched):
    """
    The ordinary path: the sweep landed it, the kitchen has the ticket, and the
    booking that follows is only saying the box is finished.
    """
    order = _order(OrderStatusEnum.ARRIVED_AT_POS)

    assert await order_service.stamp_packed(None, order, note="its run left")

    assert watched["landed"] == []
    assert watched["packed"] == [OrderStatusEnum.PACKED]


async def test_an_order_past_packing_is_still_left_alone(watched):
    """
    A second booking on an order already out with a driver. `land` must not fire
    for it either — the guard is `confirmed`, not "not yet packed" — and the
    transition guard still stops the stamp.
    """
    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY)

    assert not await order_service.stamp_packed(None, order, note="re-dispatched")

    assert watched["landed"] == []
    assert watched["packed"] == []
