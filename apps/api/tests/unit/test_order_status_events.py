"""
That a status change cannot happen without being recorded.

The whole value of `order_status_events` is coverage: `Order.status` is
assigned from thirteen places and only two of them go through
`order_service.update_status`, so a recorder each writer has to remember to call
would already be incomplete. The listener is what makes that structural, and
these tests are what stop it quietly regressing — a `set` event that stops
firing, or an actor context that leaks into the next request, would both look
exactly like nothing happening.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import (
    StatusSourceEnum,
    acting_as,
    pending_events,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _order(status=OrderStatusEnum.CREATED) -> Order:
    return Order(id=uuid.uuid4(), status=status, order_number="MM-EVT-1")


def _statuses(order: Order) -> list[str]:
    return [event[0] for event in pending_events(order)]


def _clear(order: Order) -> Order:
    inspect(order).info.pop("pending_status_events", None)
    return order


# ── coverage ──────────────────────────────────────────────────────────────────


def test_building_an_order_records_its_first_status():
    """The constructor sets `status`, which is a transition like any other.

    Without this the first row of every order's history would be missing, and
    `created` is the one step the admin's timeline could always fill.
    """
    order = _order()
    assert _statuses(order) == ["created"]
    assert pending_events(order)[0][1] is None, "there was nothing before it"


def test_a_direct_assignment_is_recorded():
    """`payment_service` writes the column directly, six times over."""
    order = _clear(_order())
    order.status = OrderStatusEnum.CONFIRMED
    assert _statuses(order) == ["confirmed"]


def test_a_raw_string_is_recorded_like_an_enum():
    """
    `pos_order_service` assigns `"confirmed"` and `"cancelled"` as literals in
    three places. A recorder that assumed an enum member would drop them, and
    dropping the counter's own transitions is exactly the gap this replaces.
    """
    order = _clear(_order(OrderStatusEnum.CONFIRMED))
    order.status = "cancelled"
    assert _statuses(order) == ["cancelled"]
    assert pending_events(order)[0][1] == "confirmed"


def test_the_previous_status_is_never_a_sentinel():
    """
    SQLAlchemy hands back a `LoaderCallableStatus` member when there is no old
    value, and it is an `Enum` whose `.value` is `4`. A generic enum check
    accepted it and wrote `previous_status = '4'` on the first row of every
    order — a real bug this pins.
    """
    order = _order()
    previous = pending_events(order)[0][1]
    assert previous is None
    assert previous != "4"


def test_re_assigning_the_same_status_records_nothing():
    """A webhook replayed ten times over a day must not make ten rows."""
    order = _clear(_order(OrderStatusEnum.PACKED))
    order.status = OrderStatusEnum.PACKED
    order.status = "packed"
    assert _statuses(order) == []


def test_the_whole_journey_is_recorded_in_order():
    order = _order()
    order.status = OrderStatusEnum.CONFIRMED
    order.status = OrderStatusEnum.PACKED
    order.status = OrderStatusEnum.OUT_FOR_DELIVERY
    order.status = OrderStatusEnum.DELIVERED
    assert _statuses(order) == [
        "created",
        "confirmed",
        "packed",
        "out_for_delivery",
        "delivered",
    ]


def test_a_re_dispatch_keeps_both_attempts():
    """
    An undelivered order goes back to packed and out again. Both dispatches are
    real and both are kept — the timeline shows the later one, and the earlier
    one is why anybody would ask.
    """
    order = _clear(_order(OrderStatusEnum.OUT_FOR_DELIVERY))
    order.status = OrderStatusEnum.UNDELIVERED
    order.status = OrderStatusEnum.PACKED
    order.status = OrderStatusEnum.OUT_FOR_DELIVERY
    assert _statuses(order) == ["undelivered", "packed", "out_for_delivery"]


# ── attribution ───────────────────────────────────────────────────────────────


def test_an_unattributed_change_says_so_rather_than_guessing():
    order = _clear(_order())
    order.status = OrderStatusEnum.CONFIRMED
    assert pending_events(order)[0][2].source == StatusSourceEnum.SYSTEM.value


def test_a_context_names_who_moved_it():
    admin_id = uuid.uuid4()
    order = _clear(_order(OrderStatusEnum.CONFIRMED))
    with acting_as(
        StatusSourceEnum.ADMIN.value,
        actor_id=admin_id,
        actor_label="fatema@meltingmoments.ae",
        note="customer called",
    ):
        order.status = OrderStatusEnum.PACKED

    actor = pending_events(order)[0][2]
    assert actor.source == "admin"
    assert actor.actor_id == admin_id
    assert actor.actor_label == "fatema@meltingmoments.ae"
    assert actor.note == "customer called"


def test_the_context_does_not_leak_past_its_block():
    """
    A `ContextVar` left set would attribute the next request's transitions to
    the previous request's admin — a wrong name on a real record, which is
    worse than no name at all.
    """
    inside = _clear(_order(OrderStatusEnum.CONFIRMED))
    with acting_as(StatusSourceEnum.ADMIN.value, actor_label="someone@shop.ae"):
        inside.status = OrderStatusEnum.PACKED

    after = _clear(_order(OrderStatusEnum.CONFIRMED))
    after.status = OrderStatusEnum.PACKED
    assert pending_events(after)[0][2].actor_label is None
    assert pending_events(after)[0][2].source == StatusSourceEnum.SYSTEM.value


def test_the_context_survives_an_exception():
    order = _clear(_order())
    try:
        with acting_as(StatusSourceEnum.ADMIN.value, actor_label="a@b.c"):
            raise RuntimeError("something failed mid-transition")
    except RuntimeError:
        pass
    order.status = OrderStatusEnum.CONFIRMED
    assert pending_events(order)[0][2].source == StatusSourceEnum.SYSTEM.value


# ── the clock ─────────────────────────────────────────────────────────────────


def test_a_courier_moment_beats_our_own():
    """
    Lalamove retries an unacknowledged event for a full day, so the moment we
    handle a push is not the moment the rider collected. Recording ours would
    make "picked up at" mean "webhook processed at" — a different fact, on a
    customer's timeline.
    """
    collected = NOW - timedelta(hours=3)
    order = _clear(_order(OrderStatusEnum.PACKED))
    with acting_as(
        StatusSourceEnum.COURIER.value, actor_label="lalamove", at=collected
    ):
        order.status = OrderStatusEnum.OUT_FOR_DELIVERY

    assert pending_events(order)[0][3] == collected


def test_without_a_courier_moment_the_stamp_is_now():
    """noon Send's status push carries no usable timestamp at all."""
    order = _clear(_order(OrderStatusEnum.PACKED))
    before = datetime.now(timezone.utc)
    with acting_as(StatusSourceEnum.COURIER.value, actor_label="noon_send"):
        order.status = OrderStatusEnum.OUT_FOR_DELIVERY

    assert before <= pending_events(order)[0][3] <= datetime.now(timezone.utc)
