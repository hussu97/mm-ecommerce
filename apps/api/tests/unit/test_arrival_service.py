"""
When the shop is told to make an order.

`confirmed` used to mean the money arrived *and* the register heard about it.
For a zone whose orders ride a shared van those are hours apart, and collapsing
them meant the ticket printed before the courier existed — so it printed
`MM-20260819-001`, which is the fifteen-character string the seven-digit courier
reference exists to keep out of a driver's mouth.

The rule these tests pin: **the shop is told when the van is booked.** For a
batched zone that is the close of the run; for everything else — a pickup, a
third-party zone, a courier with no schedule — it is the same instant as the
confirmation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum
from app.services import arrival_service

#: Comfortably ahead of now, so the batched case is genuinely waiting.
LATER = datetime.now(timezone.utc) + timedelta(hours=2)


def _order(status: OrderStatusEnum = OrderStatusEnum.CONFIRMED, **overrides) -> Order:
    order = Order(
        id=uuid.uuid4(),
        order_number="MM-20260819-001",
        status=status,
        source=OrderSourceEnum.ONLINE.value,
        branch_id=uuid.uuid4(),
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    # Constructing with a status queues an event for it. These tests are about
    # what `land` records, so the history starts empty.
    from sqlalchemy import inspect as sa_inspect

    sa_inspect(order).info.pop("pending_status_events", None)
    return order


def _run(dispatch_at: datetime, *, open_: bool = True):
    return SimpleNamespace(dispatch_at=dispatch_at, is_open=open_)


@pytest.fixture
def stubs(monkeypatch):
    """The world around a scheduling decision, recorded rather than run."""
    from app.services import batching_service

    calls: dict[str, list] = {"landed": [], "reserved": []}

    async def land(db, order, *, because):
        calls["landed"].append(order)
        calls.setdefault("reasons", []).append(because)
        return True

    monkeypatch.setattr(arrival_service, "land", land)

    def reserve_returns(batch):
        async def reserve(db, order, **kwargs):
            calls["reserved"].append(order)
            return batch

        monkeypatch.setattr(batching_service, "reserve", reserve)

    calls["reserve_returns"] = reserve_returns  # type: ignore[assignment]
    return calls


@pytest.fixture
def shop_is_open(monkeypatch):
    async def now(db, order):
        return datetime.now(timezone.utc)

    monkeypatch.setattr(arrival_service, "_next_working_moment", now)


@pytest.mark.asyncio
async def test_a_batched_order_arrives_when_its_run_leaves(stubs, shop_is_open):
    """
    The case the status exists for. Two hours between the money and the kitchen,
    and the ticket that eventually prints has a courier on it.
    """
    stubs["reserve_returns"](_run(LATER))
    order = _order()

    await arrival_service.schedule(None, order)

    assert order.arrives_at == LATER
    assert stubs["landed"] == [], "the counter was told before the van was booked"


@pytest.mark.asyncio
async def test_an_order_with_no_run_to_wait_for_arrives_at_once(stubs, shop_is_open):
    """
    A pickup, a third-party zone, a courier nobody put on a schedule. Landed
    inline rather than left for the sweep: a minute between a customer paying
    cash at the counter and the counter hearing about it is a minute of
    somebody standing there.
    """
    stubs["reserve_returns"](None)
    order = _order()

    await arrival_service.schedule(None, order)

    assert stubs["landed"] == [order]


@pytest.mark.asyncio
async def test_a_run_that_has_already_gone_does_not_hold_an_order_back(
    stubs, shop_is_open
):
    """
    `reserve` answering with a closed run means the order was added to something
    already dispatched. Reading `dispatch_at` off it would date the arrival in
    the past and, worse, read as batched — so it is treated as no run at all.
    """
    stubs["reserve_returns"](_run(LATER, open_=False))
    order = _order()

    await arrival_service.schedule(None, order)

    assert stubs["landed"] == [order]


@pytest.mark.asyncio
async def test_a_counter_sale_has_no_arrival_to_schedule(stubs):
    """Somebody is holding it. There is nobody to tell."""
    order = _order(source=OrderSourceEnum.CASHIER.value)

    assert await arrival_service.schedule(None, order) is None
    assert order.arrives_at is None
    assert stubs["reserved"] == []


@pytest.mark.asyncio
async def test_a_shut_shop_is_not_told_until_it_opens(stubs, monkeypatch):
    """
    An order placed at 03:00 used to land on a dark counter and ring at nobody
    until somebody arrived to silence it.
    """
    opens = datetime.now(timezone.utc) + timedelta(hours=6)

    async def at_opening(db, order):
        return opens

    monkeypatch.setattr(arrival_service, "_next_working_moment", at_opening)
    stubs["reserve_returns"](None)
    order = _order()

    await arrival_service.schedule(None, order)

    assert order.arrives_at == opens
    assert stubs["landed"] == []


# ── landing ───────────────────────────────────────────────────────────────────


class _Db:
    async def rollback(self):
        return None


@pytest.fixture
def quiet_publish(monkeypatch):
    """`arrived_at_pos` publishes to the register; that has its own tests."""
    from app.services import order_service

    async def publish(db, order, branch=None):
        return None

    monkeypatch.setattr(order_service, "publish_to_register", publish)


@pytest.mark.asyncio
async def test_a_courier_that_refuses_still_lets_the_kitchen_know(
    monkeypatch, quiet_publish
):
    """
    The ordering that matters most here. A cake is paid for and has to be made
    whatever the courier said, so the arrival is *sequenced* after the dispatch
    and never *conditional* on it. Getting this backwards would mean an empty
    Lalamove wallet silently stopped the kitchen.
    """
    from app.services import batching_service

    async def explode(db, order, **kwargs):
        raise RuntimeError("wallet is empty")

    monkeypatch.setattr(batching_service, "assign_or_dispatch", explode)
    order = _order()

    assert await arrival_service.land(_Db(), order, because="test") is True
    assert order.status == OrderStatusEnum.ARRIVED_AT_POS


@pytest.mark.asyncio
async def test_an_order_that_moved_on_without_us_is_left_alone(
    monkeypatch, quiet_publish
):
    """
    A courier reporting a pickup, or an admin marking the box packed, both leave
    `confirmed` without passing through here. The sweep must not drag such an
    order backwards onto a status it has already overtaken.
    """
    from app.services import batching_service

    async def never(db, order, **kwargs):
        raise AssertionError("a driver was called for an order already gone")

    monkeypatch.setattr(batching_service, "assign_or_dispatch", never)
    order = _order(status=OrderStatusEnum.OUT_FOR_DELIVERY)

    assert await arrival_service.land(_Db(), order, because="test") is False
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY


# ── the shape of the chain ────────────────────────────────────────────────────


def test_a_driver_collecting_skips_packed_rather_than_inventing_it():
    """
    On a zone we dispatch ourselves the courier's PICKED_UP is usually the first
    news that the box is finished. The tempting move is to write a `packed` on
    the way through so the chain has no holes in it — but `packed_at` is shown
    to the customer and read by the kitchen reports, and a timestamp meaning
    "the moment we heard about the driver" is wrong in both.
    """
    from app.services.order_lifecycle import VALID_TRANSITIONS

    assert (
        OrderStatusEnum.OUT_FOR_DELIVERY
        in VALID_TRANSITIONS[OrderStatusEnum.ARRIVED_AT_POS]
    ), "a collected order would be stranded at arrived_at_pos"


def test_confirmed_still_reaches_packed_without_arriving():
    """
    The sweep normally gets there first, but an admin correcting an order — or
    a register running ahead of a tick — is describing a box that physically
    exists. Refusing that because our own bookkeeping has not caught up would be
    the tail wagging the dog.
    """
    from app.services.order_lifecycle import VALID_TRANSITIONS

    assert OrderStatusEnum.PACKED in VALID_TRANSITIONS[OrderStatusEnum.CONFIRMED]


def test_an_arrived_order_cannot_go_back_to_confirmed():
    """
    Closing a check on the register writes `confirmed`, quietly skipping when
    the map refuses. It has to refuse here: an order on a counter that reverted
    to `confirmed` would be handed to the arrival sweep a second time, printed
    again on every terminal in the branch, and — on a zone we dispatch — sent a
    second van.
    """
    from app.services.order_lifecycle import VALID_TRANSITIONS

    assert (
        OrderStatusEnum.CONFIRMED
        not in VALID_TRANSITIONS[OrderStatusEnum.ARRIVED_AT_POS]
    )


# ── the status history ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_gateway_is_not_recorded_as_having_told_the_shop(
    monkeypatch, quiet_publish
):
    """
    The inline arrival runs inside `_consequences` for `confirmed`, which on the
    ordinary route is inside `payment_service`'s own
    `acting_as(PAYMENT, actor_label="stripe", note="payment_intent.succeeded")`.

    Inherited, the history would say Stripe put the order on the register and
    give a payment event id as the reason. Stripe reported money; it did not
    tell a kitchen anything.
    """
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services import batching_service

    async def nothing(db, order, **kwargs):
        return None

    monkeypatch.setattr(batching_service, "assign_or_dispatch", nothing)
    order = _order()

    with acting_as(
        StatusSourceEnum.PAYMENT.value,
        actor_label="stripe",
        note="payment_intent.succeeded",
    ):
        await arrival_service.land(_Db(), order, because="its run left")

    events = _pending_events(order)
    assert len(events) == 1
    status, _previous, actor, _at = events[0]
    assert status == OrderStatusEnum.ARRIVED_AT_POS
    assert actor.source == StatusSourceEnum.SYSTEM.value
    assert actor.actor_label != "stripe"
    assert actor.note == "its run left"


@pytest.mark.asyncio
async def test_an_arrival_is_recorded_like_any_other_status(monkeypatch, quiet_publish):
    """
    Not by this module's own hand. `order_status_events` is written by an
    attribute listener on `Order.status`, so a status is recorded because it was
    *assigned*, not because somebody remembered to record it — which is the
    property that survives the next writer.
    """
    from app.services import batching_service

    async def nothing(db, order, **kwargs):
        return None

    monkeypatch.setattr(batching_service, "assign_or_dispatch", nothing)
    order = _order()

    await arrival_service.land(_Db(), order, because="its arrival fell due")

    events = _pending_events(order)
    assert [e[0] for e in events] == [OrderStatusEnum.ARRIVED_AT_POS]
    assert events[0][1] == OrderStatusEnum.CONFIRMED


def test_the_sweep_says_why_each_order_landed():
    """
    Three things bring an order to the sweep and they are different questions
    later — the third most of all, because it means the confirmation failed to
    stamp an arrival and this is covering for it.
    """
    from app.models.order_delivery import OrderDelivery

    on_a_run = _order()
    on_a_run.delivery = OrderDelivery(
        order_id=on_a_run.id, provider="lalamove", batch_id=uuid.uuid4()
    )
    assert arrival_service._why(on_a_run) == "its run left"

    held = _order(arrives_at=datetime.now(timezone.utc))
    assert arrival_service._why(held) == "its arrival fell due"

    assert "swept up" in arrival_service._why(_order())


def _pending_events(order):
    """The status rows the listener has queued for the next flush."""
    from sqlalchemy import inspect as sa_inspect

    return sa_inspect(order).info.get("pending_status_events", [])
