"""
`orders.channels` — how each sales channel behaves — and the lifecycle reading it.

The storefront, counter and marketplace rows pin the behaviour the inline
`source == "online"` gates had before the table existed; the custom rows pin
what is different about a bespoke cake: no automatic courier, no card refund,
consumption at packing, and its trading day taken from the hand-over.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect

import app.services.orders.order_service  # noqa: F401 — registers the listeners
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum
from app.services.orders import channels, order_lifecycle

# ── the table ─────────────────────────────────────────────────────────────────


def test_the_storefront_books_its_own_courier_and_refunds_its_own_card():
    policy = channels.policy_for("online")
    assert policy.books_courier_automatically
    assert policy.manages_courier
    assert policy.auto_refunds_card
    assert policy.consumes_stock_at == OrderStatusEnum.CONFIRMED
    assert policy.customer_email_templates is None


@pytest.mark.parametrize("source", ["cashier", "aggregator"])
def test_the_counter_and_the_marketplaces_run_none_of_the_courier_machinery(source):
    policy = channels.policy_for(source)
    assert not policy.books_courier_automatically
    assert not policy.manages_courier
    assert not policy.auto_refunds_card
    assert policy.customer_email_templates == frozenset()
    assert not policy.notifies_owner_on_order


def test_a_custom_order_is_booked_by_a_person_and_consumed_when_packed():
    policy = channels.policy_for(OrderSourceEnum.CUSTOM)
    assert not policy.books_courier_automatically
    assert policy.manages_courier
    assert not policy.auto_refunds_card
    assert policy.consumes_stock_at == OrderStatusEnum.PACKED
    assert policy.customer_emails_need_booked_courier
    assert "order_confirmation.html" not in policy.customer_email_templates


def test_an_unknown_source_keeps_the_old_inline_behaviour():
    """No courier or refund machinery (the old `== online` gate was false) and
    every email (the old counter-sale gate was false)."""
    for source in (None, "", "martian"):
        policy = channels.policy_for(source)
        assert not policy.books_courier_automatically
        assert not policy.auto_refunds_card
        assert policy.customer_email_templates is None


# ── the lifecycle reading it ──────────────────────────────────────────────────


def _order(status: OrderStatusEnum, source: str, **overrides) -> Order:
    order = Order(
        id=uuid.uuid4(),
        status=status,
        order_number="CO-LC-1",
        source=source,
        pos_status=None,
        branch_id=uuid.uuid4(),
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    inspect(order).info.pop("pending_status_events", None)
    return order


class _Db:
    async def execute(self, stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def get(self, _model, _id):
        # A branch whose trading day starts at 04:00.
        return SimpleNamespace(business_day_start="04:00")


@pytest.fixture
def calls(monkeypatch):
    from app.services.couriers import courier_service
    from app.services.delivery import arrival_service
    from app.services.inventory import source_event_service
    from app.services.orders import order_service
    from app.services.payments import payment_service
    from app.services.pos import business_day_service

    seen: dict[str, list] = {
        "accept": [],
        "schedule": [],
        "publish": [],
        "dispatch": [],
        "courier_cancel": [],
        "refund": [],
        "cancellation": [],
    }

    async def accept_order(db, *, order, user, occurred_at=None, **_):
        seen["accept"].append(occurred_at)

    async def schedule(db, order):
        seen["schedule"].append(order)

    async def publish(db, order, branch=None):
        seen["publish"].append(order)

    async def dispatch(db, order, *, lock=True):
        seen["dispatch"].append(order)

    async def courier_cancel(db, order):
        seen["courier_cancel"].append(order)

    async def refund(db, order):
        seen["refund"].append(order)

    async def record_order_cancellation(db, order, *, pre_packing):
        seen["cancellation"].append(pre_packing)

    async def resolve_timezone(db):
        return ZoneInfo("Asia/Dubai")

    async def current_business_date(db, branch):
        return "2026-09-26"

    monkeypatch.setattr(source_event_service, "accept_order", accept_order)
    monkeypatch.setattr(
        source_event_service, "record_order_cancellation", record_order_cancellation
    )
    monkeypatch.setattr(arrival_service, "schedule", schedule)
    monkeypatch.setattr(order_service, "publish_to_register", publish)
    monkeypatch.setattr(courier_service, "dispatch", dispatch)
    monkeypatch.setattr(courier_service, "cancel", courier_cancel)
    monkeypatch.setattr(payment_service, "refund_order", refund)
    monkeypatch.setattr(business_day_service, "resolve_timezone", resolve_timezone)
    monkeypatch.setattr(
        business_day_service, "current_business_date", current_business_date
    )
    return seen


@pytest.mark.asyncio
async def test_confirming_a_website_order_consumes_and_schedules_it(calls):
    order = _order(OrderStatusEnum.CREATED, "online")
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CONFIRMED)
    assert calls["accept"] == [None]
    assert calls["schedule"] == [order]


@pytest.mark.asyncio
async def test_confirming_a_custom_order_consumes_nothing_and_books_nothing(calls):
    order = _order(OrderStatusEnum.CREATED, "custom")
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CONFIRMED)
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.ARRIVED_AT_POS)
    assert calls["accept"] == []
    assert calls["schedule"] == []
    assert calls["publish"] == []


@pytest.mark.asyncio
async def test_packing_a_custom_order_consumes_it_dated_now_and_books_no_courier(
    calls,
):
    order = _order(OrderStatusEnum.ARRIVED_AT_POS, "custom")
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.PACKED)
    assert len(calls["accept"]) == 1 and calls["accept"][0] is not None
    assert calls["dispatch"] == []


@pytest.mark.asyncio
async def test_packing_a_website_order_still_books_its_courier(calls):
    order = _order(OrderStatusEnum.ARRIVED_AT_POS, "online")
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.PACKED)
    assert calls["dispatch"] == [order]
    assert calls["accept"] == []


@pytest.mark.asyncio
async def test_cancelling_a_custom_order_calls_off_its_courier_but_refunds_nothing(
    calls,
):
    order = _order(OrderStatusEnum.UNDELIVERED, "custom")
    order.items = []
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CANCELLED)
    assert calls["courier_cancel"] == [order]
    assert calls["refund"] == []
    # What packing consumed was a cake that was made: no disposition to raise.
    assert calls["cancellation"] == []
    # Filed under today's trading day, so any courier cost it ran up is reported.
    assert order.business_date == "2026-09-26"


@pytest.mark.asyncio
async def test_cancelling_a_website_order_still_refunds_and_reverses(calls):
    order = _order(OrderStatusEnum.CONFIRMED, "online")
    order.items = []
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.CANCELLED)
    assert calls["refund"] == [order]
    assert calls["cancellation"] == [True]


@pytest.mark.asyncio
async def test_delivery_stamps_the_hand_over_for_every_channel(calls):
    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY, "online")
    await order_lifecycle.transition(_Db(), order, OrderStatusEnum.DELIVERED)
    assert order.delivered_at is not None
    assert order.business_date is None  # a website order's day is its register's


@pytest.mark.asyncio
async def test_a_custom_order_takes_its_trading_day_from_the_hand_over(calls):
    """00:30 Dubai on the 28th is still the 27th's trading day (04:00 cut-off)."""
    order = _order(OrderStatusEnum.PACKED, "custom")
    handed_over = datetime(2026, 9, 27, 20, 30, tzinfo=timezone.utc)  # 00:30 +04
    with acting_as(StatusSourceEnum.ADMIN.value, at=handed_over):
        await order_lifecycle.transition(_Db(), order, OrderStatusEnum.DELIVERED)
    assert order.delivered_at == handed_over
    assert order.business_date == "2026-09-27"
    assert order.closed_at == handed_over
    assert order.pos_status is None  # never on a register


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start", [OrderStatusEnum.CONFIRMED, OrderStatusEnum.ARRIVED_AT_POS]
)
@pytest.mark.parametrize(
    "target", [OrderStatusEnum.DELIVERED, OrderStatusEnum.OUT_FOR_DELIVERY]
)
async def test_an_unpacked_custom_order_cannot_leave_the_shop(calls, start, target):
    """The map's shortcuts past `packed` would finish it with nothing consumed."""
    from app.core.exceptions import BadRequestError

    order = _order(start, "custom")
    with pytest.raises(BadRequestError):
        await order_lifecycle.transition(_Db(), order, target)
    assert order.status == start
    # A courier webhook replaying the same move is declined quietly.
    assert not await order_lifecycle.transition(_Db(), order, target, on_invalid="skip")


@pytest.mark.asyncio
async def test_a_cancelled_custom_order_cannot_be_recovered_to_delivered(calls):
    order = _order(OrderStatusEnum.CANCELLED, "custom")
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await order_lifecycle.transition(
            _Db(),
            order,
            OrderStatusEnum.DELIVERED,
            extra_from=order_lifecycle.ADMIN_RECOVERABLE[OrderStatusEnum.DELIVERED],
        )


@pytest.mark.asyncio
async def test_a_website_order_keeps_its_shortcut_to_delivered(calls):
    order = _order(OrderStatusEnum.CONFIRMED, "online")
    assert await order_lifecycle.transition(_Db(), order, OrderStatusEnum.DELIVERED)
