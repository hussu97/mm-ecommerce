"""
An order nobody has paid for is not the counter's problem yet.

`create_order` used to put every storefront order on a branch's register the
moment the row was written — check number, business day, `pos_status =
pending` — and push the kitchen about it. That is one line after the insert,
which for a card order is long before anybody has been charged: the status is
`created` until Stripe's `payment_intent.succeeded` lands, and for a checkout
that was abandoned, a card that was never entered, or a 3-D Secure prompt that
was ignored, it stays `created` forever.

So the iPad showed orders on its incoming list, sounded the unaccepted-order
alarm for each one, and offered a cashier an Accept button for a sale that had
not happened. Cash and zero-total orders confirm at checkout and are genuinely
work; every other method has an event to wait for.

Two halves, because a write-path fix cannot reach rows that are already wrong:

* **Publishing** now hangs off the confirmation rather than the insert, from
  whichever route confirms — Stripe, cash, a zero total, an admin by hand.
* **Reading** filters unpaid storefront orders out of every register query, so
  the ones already sitting in production stop showing without a migration, and
  reappear correctly if their payment ever does land.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum, PosOrderStatusEnum
from app.services.orders import order_service
from app.services.pos import pos_order_service
from app.services.providers.base import GatewayEvent, PaymentEventType

BRANCH = SimpleNamespace(
    id=uuid.uuid4(),
    reference="K001",
    name="Melting Moments Cakes",
    is_active=True,
    deleted_at=None,
)


def _order(**overrides) -> Order:
    order = Order(
        order_number="MM-20260808-001",
        email="customer@example.com",
        delivery_method=DeliveryMethodEnum.PICKUP,
        status=OrderStatusEnum.CREATED,
        source=OrderSourceEnum.ONLINE.value,
        branch_id=BRANCH.id,
        shipping_address_snapshot={"first_name": "Hussain", "phone": "+971500000000"},
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    return order


# ── The rule, stated once ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", list(OrderStatusEnum))
def test_a_counter_check_is_always_the_registers_business(status):
    """
    A cashier's own order sits at `created` for its entire life on the
    register — that is what an order being rung up *is*, and the person who
    opened it is standing in front of it. The unpaid rule is about the
    storefront and must never touch the counter.
    """
    order = _order(status=status, source=OrderSourceEnum.CASHIER.value)
    assert pos_order_service.is_paid_for(order) is True


def test_a_storefront_order_nobody_has_paid_for_is_not():
    assert (
        pos_order_service.is_paid_for(_order(status=OrderStatusEnum.CREATED)) is False
    )


def test_nor_is_one_whose_card_was_declined():
    order = _order(status=OrderStatusEnum.PAYMENT_FAILED)
    assert pos_order_service.is_paid_for(order) is False


@pytest.mark.parametrize(
    "status",
    [s for s in OrderStatusEnum if s not in pos_order_service.UNPAID_ONLINE_STATUSES],
)
def test_every_other_storefront_status_is_the_registers_business(status):
    """
    Named exhaustively rather than as a shortlist, so a new `OrderStatusEnum`
    member has to be classified here instead of silently defaulting to hidden.
    A refunded or cancelled order still belongs on the reports and the history;
    what keeps it off the *incoming* list is its `pos_status`, not this.
    """
    assert pos_order_service.is_paid_for(_order(status=status)) is True


def test_the_sql_filter_is_built_from_the_same_two_facts():
    """
    `paid_for_clause` is what every register query actually applies, and a
    Python rule the SQL does not mirror is the shape of the original bug.
    """
    rendered = str(
        pos_order_service.paid_for_clause().compile(
            compile_kwargs={"literal_binds": True}
        )
    )
    assert "orders.source != 'online'" in rendered
    assert "'created'" in rendered
    assert "'payment_failed'" in rendered
    # Not an AND: a counter check must pass on the source alone.
    assert " OR " in rendered


# ── Publishing hangs off the confirmation ─────────────────────────────────────


@pytest.fixture
def attach(monkeypatch):
    """`attach_online_order` with the trading day and check number stubbed."""

    async def get_or_open(db, branch, **_kwargs):
        return SimpleNamespace(business_date="2026-08-08")

    async def next_check_number(db, branch_id, business_date, reset_daily):
        return 7

    async def settings(db):
        return SimpleNamespace(order_number_reset_daily=True)

    monkeypatch.setattr(
        pos_order_service.business_day_service, "get_or_open", get_or_open
    )
    monkeypatch.setattr(pos_order_service, "_next_check_number", next_check_number)
    monkeypatch.setattr(pos_order_service, "_settings", settings)


@pytest.fixture
def pushed(monkeypatch):
    sent = AsyncMock(return_value=1)
    monkeypatch.setattr(order_service.push_service, "notify_order_placed", sent)
    return sent


async def test_publishing_puts_it_on_the_register_and_rings_the_counter(attach, pushed):
    order = _order(status=OrderStatusEnum.CONFIRMED)

    await order_service.publish_to_register(AsyncMock(), order, BRANCH)

    assert order.is_pos is True
    assert order.pos_status == PosOrderStatusEnum.PENDING.value
    assert order.check_number == 7
    pushed.assert_awaited_once()


async def test_publishing_twice_does_not_ring_the_alarm_twice(attach, pushed):
    """
    Several routes confirm an order and some of them overlap — a cash order
    confirmed by `create_order` and then asked for a payment session anyway.
    The attach already guards on the check number; the push has to guard on the
    same fact, because the one thing that must not repeat is the alarm.
    """
    order = _order(status=OrderStatusEnum.CONFIRMED)

    await order_service.publish_to_register(AsyncMock(), order, BRANCH)
    await order_service.publish_to_register(AsyncMock(), order, BRANCH)

    assert pushed.await_count == 1
    assert order.check_number == 7


async def test_a_counter_order_is_never_published_this_way(attach, pushed):
    """
    A cashier's order is already on the register — it was opened there. Running
    the storefront attach over it would re-issue its check number and push the
    kitchen about a customer standing at the till.
    """
    order = _order(source=OrderSourceEnum.CASHIER.value)

    await order_service.publish_to_register(AsyncMock(), order, BRANCH)

    assert order.check_number is None
    pushed.assert_not_awaited()


async def test_a_branch_it_was_not_given_is_looked_up(attach, pushed):
    """
    The Stripe webhook and the admin console both confirm an order without
    holding its branch. `orders.branch_id` is NOT NULL and was stamped at
    insert, so the kitchen is always knowable from the row itself.
    """
    order = _order(status=OrderStatusEnum.CONFIRMED)
    db = AsyncMock()
    db.get = AsyncMock(return_value=BRANCH)

    await order_service.publish_to_register(db, order)

    db.get.assert_awaited_once()
    assert order.branch_id == BRANCH.id
    assert order.pos_status == PosOrderStatusEnum.PENDING.value


async def test_a_register_that_will_not_take_it_does_not_lose_the_sale(pushed):
    """
    Best-effort, and unchanged in that: a business day that will not open is
    not a reason to raise out of a payment webhook that has already taken the
    customer's money. What this misses is findable by
    `is_pos = false AND source = 'online'`.
    """
    order = _order(status=OrderStatusEnum.CONFIRMED)

    with patch.object(
        pos_order_service,
        "attach_online_order",
        new=AsyncMock(side_effect=RuntimeError("no business day")),
    ):
        await order_service.publish_to_register(AsyncMock(), order, BRANCH)

    assert order.check_number is None


# ── Every confirmation route publishes ────────────────────────────────────────


def _gateway_event(event_type: PaymentEventType) -> GatewayEvent:
    """One webhook in the application's own words, whichever gateway sent it."""
    return GatewayEvent(
        event_id="evt_test",
        event_type=event_type,
        raw_type="test",
        order_number="MM-20260808-001",
        payment_id="pi_test",
    )


async def test_a_settled_payment_schedules_the_order_onto_the_kitchen(monkeypatch):
    """
    The moment a card order becomes work. Before it, the customer had only
    opened a payment page.

    It *schedules* rather than publishes: settling the money is what decides
    when the shop will be told, and for a batched zone that is the close of the
    run this call puts the order on. The publish itself happens at
    `arrived_at_pos`, which for most zones is the following tick and for a
    batched one is hours away.

    Written against `_handle_payment_succeeded` rather than a gateway, because
    that handler is the one place both Stripe and Ziina arrive at — the point
    of `GatewayEvent` is that there is nothing below it to say twice.
    """
    from app.services.delivery import arrival_service
    from app.services.inventory import source_event_service
    from app.services.payments import payment_service

    order = _order()
    scheduled = AsyncMock(return_value=None)

    monkeypatch.setattr(arrival_service, "schedule", scheduled)
    inventory_acceptance = AsyncMock(return_value=None)
    monkeypatch.setattr(source_event_service, "accept_order", inventory_acceptance)
    monkeypatch.setattr(
        payment_service.order_service, "to_response", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_order_confirmation", AsyncMock()
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_owner_order_notification", AsyncMock()
    )

    await payment_service._handle_payment_succeeded(
        AsyncMock(), order, _gateway_event(PaymentEventType.SUCCEEDED)
    )

    assert order.status == OrderStatusEnum.CONFIRMED
    scheduled.assert_awaited_once()
    inventory_acceptance.assert_awaited_once()


async def test_a_declined_card_publishes_nothing(monkeypatch):
    from app.services.payments import payment_service

    order = _order()
    published = AsyncMock()

    monkeypatch.setattr(payment_service.order_service, "publish_to_register", published)
    monkeypatch.setattr(
        payment_service.order_service, "to_response", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_payment_failed", AsyncMock()
    )

    await payment_service._handle_payment_failed(
        AsyncMock(), order, _gateway_event(PaymentEventType.FAILED)
    )

    assert order.status == OrderStatusEnum.PAYMENT_FAILED
    published.assert_not_awaited()
