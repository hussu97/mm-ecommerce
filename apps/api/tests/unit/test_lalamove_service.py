"""
What a courier update is allowed to do to an order.

The webhook receiver is effectively a second writer on the orders table, and
Lalamove is explicit that its pushes can arrive out of order and more than
once. These tests cover the consequences of that: a late update must not rewind
a delivered order, a failed booking must not cancel a paid one, and a settled
order must not be reopened by something arriving hours after the fact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import pending_events
from sqlalchemy import inspect
from app.models.order_delivery import OrderDelivery
from app.services import batching_service, lalamove_service

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
COURIER_ID = "3463513590991397204"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)


class _FakeDb:
    """Answers exactly one kind of query: give me the order behind this id."""

    def __init__(self, order):
        self.order = order

    async def execute(self, _stmt):
        return _FakeResult(self.order)

    async def get(self, _model, _pk):
        # A failed dispatch reads the branch to find out when the counter shuts,
        # so that a retry is not scheduled into a dark shop. Nothing here has a
        # branch; None is the "hours unknown, treat as always open" answer the
        # service is written to accept.
        return None


def _delivery(**overrides) -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="lalamove",
        zone_name="Sharjah City",
        fee_charged=Decimal("15.00"),
        courier_order_id=COURIER_ID,
        courier_status="ON_GOING",
        status_updated_at=NOW,
    )
    for key, value in overrides.items():
        setattr(delivery, key, value)
    return delivery


def _order(status=OrderStatusEnum.PACKED) -> Order:
    """A real `Order`, not a stand-in.

    The status listener in `order_status_event` fires on attribute assignment,
    so a `SimpleNamespace` would take `order.status = DELIVERED` silently and
    record nothing — and the moment a courier reports is now kept in that
    history rather than in a column on `order_deliveries`. `pending_events`
    reads it back without needing a flush.
    """
    order = Order(id=uuid.uuid4(), status=status, order_number="MM-1")
    # Building it *at* this status is itself a transition, and the listener
    # rightly notes it. These tests are about what the webhook does next, so the
    # order starts with an empty history — the equivalent of one loaded from the
    # database rather than one just written.
    pending_events(order).clear()
    inspect(order).info.pop("pending_status_events", None)
    return order


def _event(status: str, *, at: datetime = NOW, previous: str = "ON_GOING") -> dict:
    """
    A webhook shaped like the ones production actually receives.

    `updatedAt` is deliberately built the way Lalamove really sends it — Gulf
    local time, four hours ahead, wearing a `Z`, in the `HH:MM.ss` format that
    is not a time at all — so that anything reading it instead of the epoch
    `timestamp` fails a test here rather than four hours off in the database.
    Both production orders arrived exactly like this.
    """
    local = at.astimezone(ZoneInfo("Asia/Dubai"))
    return {
        "eventType": "ORDER_STATUS_CHANGED",
        "timestamp": int(at.timestamp()),
        "data": {
            "order": {
                "orderId": COURIER_ID,
                "status": status,
                "previousStatus": previous,
                "shareLink": "https://share.lalamove.com/abc",
            },
            "updatedAt": f"{local:%Y-%m-%dT%H:%M}.00Z",
        },
    }


# ── status mapping ────────────────────────────────────────────────────────────


async def test_pickup_puts_the_order_on_the_way():
    delivery, order = _delivery(), _order(OrderStatusEnum.PACKED)
    await lalamove_service.apply_webhook(
        _FakeDb(order), _event("PICKED_UP", at=NOW + timedelta(minutes=5)), delivery
    )
    assert delivery.courier_status == "PICKED_UP"
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY
    assert [e[0] for e in pending_events(order)] == ["out_for_delivery"]


async def test_completion_marks_the_order_delivered():
    delivery = _delivery(courier_status="PICKED_UP")
    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY)
    await lalamove_service.apply_webhook(
        _FakeDb(order),
        _event("COMPLETED", at=NOW + timedelta(minutes=30), previous="PICKED_UP"),
        delivery,
    )
    assert order.status == OrderStatusEnum.DELIVERED
    assert [e[0] for e in pending_events(order)] == ["delivered"]


@pytest.mark.parametrize("status", ["CANCELED", "REJECTED", "EXPIRED"])
async def test_a_failed_booking_flags_the_delivery_but_leaves_the_order(status):
    """
    Nobody is coming for the box, but the customer has paid and it is packed.
    Cancelling their order because a driver declined would be the wrong end of
    the problem — this needs a human, so it surfaces as one.
    """
    delivery, order = _delivery(), _order(OrderStatusEnum.PACKED)
    await lalamove_service.apply_webhook(
        _FakeDb(order), _event(status, at=NOW + timedelta(minutes=2)), delivery
    )
    assert delivery.courier_status == status
    assert delivery.needs_attention is True
    assert order.status == OrderStatusEnum.PACKED


async def test_assigning_and_ongoing_do_not_move_the_order():
    """Both mean "packed and waiting". Neither is worth telling anyone about."""
    delivery = _delivery(courier_status="ASSIGNING_DRIVER")
    order = _order(OrderStatusEnum.PACKED)
    await lalamove_service.apply_webhook(
        _FakeDb(order), _event("ON_GOING", at=NOW + timedelta(minutes=1)), delivery
    )
    assert order.status == OrderStatusEnum.PACKED


# ── out-of-order and duplicate delivery ───────────────────────────────────────


async def test_a_late_update_cannot_rewind_a_delivered_order():
    """
    Lalamove: "our webhooks may not be sent in the right chronological order".
    An ON_GOING stamped before the COMPLETED we already applied is stale.
    """
    delivered_at = NOW + timedelta(minutes=30)
    delivery = _delivery(
        courier_status="COMPLETED",
        status_updated_at=delivered_at,
        delivered_at=delivered_at,
    )
    order = _order(OrderStatusEnum.DELIVERED)

    await lalamove_service.apply_webhook(
        _FakeDb(order), _event("ON_GOING", at=NOW + timedelta(minutes=5)), delivery
    )

    assert delivery.courier_status == "COMPLETED"
    assert order.status == OrderStatusEnum.DELIVERED


async def test_replaying_the_same_event_changes_nothing():
    delivery, order = _delivery(), _order(OrderStatusEnum.PACKED)
    event = _event("PICKED_UP", at=NOW + timedelta(minutes=5))

    await lalamove_service.apply_webhook(_FakeDb(order), event, delivery)
    first = pending_events(order)
    await lalamove_service.apply_webhook(_FakeDb(order), event, delivery)

    # The second pass finds the order already `out_for_delivery`, so there is no
    # transition to record — one row, not two, and the stamp is the first one.
    assert pending_events(order) == first
    assert [e[0] for e in first] == ["out_for_delivery"]
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY


async def test_a_refunded_order_is_not_reopened_by_a_late_delivery():
    """Money has already moved. A courier event does not get to undo that."""
    delivery = _delivery(courier_status="PICKED_UP")
    order = _order(OrderStatusEnum.REFUNDED)
    await lalamove_service.apply_webhook(
        _FakeDb(order),
        _event("COMPLETED", at=NOW + timedelta(hours=2), previous="PICKED_UP"),
        delivery,
    )
    assert order.status == OrderStatusEnum.REFUNDED


async def test_a_created_order_does_not_jump_to_out_for_delivery():
    """
    A pickup event for an order still sitting at "created" means our state and
    theirs have diverged. Trusting it would skip confirmation and packing.
    """
    delivery, order = _delivery(), _order(OrderStatusEnum.CREATED)
    await lalamove_service.apply_webhook(
        _FakeDb(order), _event("PICKED_UP", at=NOW + timedelta(minutes=5)), delivery
    )
    assert order.status == OrderStatusEnum.CREATED


# ── other event types ─────────────────────────────────────────────────────────


async def test_driver_details_are_recorded():
    delivery, order = _delivery(), _order()
    await lalamove_service.apply_webhook(
        _FakeDb(order),
        {
            "eventType": "DRIVER_ASSIGNED",
            "data": {
                "driver": {
                    "driverId": "79973",
                    "name": "Test Driver",
                    "phone": "+971500000000",
                    "plateNumber": "VP7815702",
                },
                "location": {"lat": 25.3304, "lng": 55.3710},
                "order": {"orderId": COURIER_ID},
                "updatedAt": (NOW + timedelta(minutes=1))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        },
        delivery,
    )
    assert delivery.driver_name == "Test Driver"
    assert delivery.driver_plate == "VP7815702"
    assert delivery.driver_latitude == Decimal("25.3304")


async def test_proof_of_delivery_is_recorded():
    delivery, order = _delivery(), _order()
    await lalamove_service.apply_webhook(
        _FakeDb(order),
        {
            "eventType": "POD_STATUS_CHANGED",
            "data": {
                "order": {
                    "orderId": COURIER_ID,
                    "status": "PICKED_UP",
                    "stops": [
                        {"deliveryCode": {"status": "Not Applicable"}},
                        {
                            "POD": {
                                "status": "DELIVERED",
                                "image": "https://example.test/pod.png",
                            }
                        },
                    ],
                },
                "updatedAt": (NOW + timedelta(minutes=20))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        },
        delivery,
    )
    assert delivery.pod_status == "DELIVERED"
    assert delivery.pod_image_url == "https://example.test/pod.png"


async def test_the_real_cost_is_captured_from_the_price_breakdown():
    """
    The number the whole exercise turns on: what the courier actually charged,
    against the fee we took.
    """
    delivery, order = _delivery(), _order()
    event = _event("COMPLETED", at=NOW + timedelta(minutes=40), previous="PICKED_UP")
    event["data"]["order"]["priceBreakdown"] = {
        "base": "20",
        "specialRequests": "5",
        "total": "25",
        "currency": "AED",
    }
    await lalamove_service.apply_webhook(_FakeDb(order), event, delivery)
    assert delivery.cost_total == Decimal("25")
    # 15 charged against 25 spent. Loss-making on its own, and visible as such.
    assert delivery.fee_charged - delivery.cost_total == Decimal("-10")


async def test_an_event_for_an_order_we_never_booked_is_ignored():
    assert (
        await lalamove_service.apply_webhook(_FakeDb(None), _event("COMPLETED")) is None
    )


async def test_an_event_with_no_order_id_is_ignored():
    payload = {"eventType": "WALLET_BALANCE_CHANGED", "data": {"amount": "100"}}
    assert await lalamove_service.apply_webhook(_FakeDb(None), payload) is None


# ── dispatch ──────────────────────────────────────────────────────────────────


async def test_a_third_party_zone_is_never_dispatched():
    """
    The point of the provider column: these zones work exactly as they did
    before, with nothing called and nobody booked.
    """
    delivery = _delivery(provider="third_party", courier_order_id=None)
    order = _order(OrderStatusEnum.PACKED)

    async def _get_delivery(_db, _order_id):
        return delivery

    original = lalamove_service.get_delivery
    lalamove_service.get_delivery = _get_delivery  # type: ignore[assignment]
    try:
        result = await lalamove_service.dispatch_order(_FakeDb(order), order)
    finally:
        lalamove_service.get_delivery = original  # type: ignore[assignment]

    assert result is delivery
    assert result.courier_order_id is None
    assert result.last_error is None


async def test_an_order_already_out_with_a_driver_is_not_booked_twice():
    delivery = _delivery(courier_status="ON_GOING")
    order = _order(OrderStatusEnum.PACKED)

    async def _get_delivery(_db, _order_id):
        return delivery

    original = lalamove_service.get_delivery
    lalamove_service.get_delivery = _get_delivery  # type: ignore[assignment]
    try:
        result = await lalamove_service.dispatch_order(_FakeDb(order), order)
    finally:
        lalamove_service.get_delivery = original  # type: ignore[assignment]

    assert result.courier_order_id == COURIER_ID
    assert result.previous_courier_order_ids in (None, [])


# ── phone numbers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+971501234567", "+971501234567"),
        ("00971501234567", "+971501234567"),
        ("971501234567", "+971501234567"),
        ("0501234567", "+971501234567"),
        ("050 123 4567", "+971501234567"),
        ("050-123-4567", "+971501234567"),
        # Nonsense is dropped rather than guessed at — a wrong number on a
        # booking strands a driver outside a building.
        ("12345", ""),
        ("", ""),
    ],
)
def test_phone_numbers_reach_e164_or_nothing(raw, expected):
    assert lalamove_service.normalise_phone(raw) == expected


async def test_nothing_is_batched_when_no_courier_is_configured():
    """
    Without credentials there is no shared run to wait for, so an order must
    not be parked in a batch that can only fail when its window closes. It
    falls through to the single-order path, which records "dispatch this by
    hand" on the order straight away — where the person packing it will see it.
    """
    delivery = _delivery(courier_order_id=None, courier_status=None, batch_id=None)
    order = _order(OrderStatusEnum.PACKED)
    delivery.polygon_id = uuid.uuid4()

    async def _get_delivery(_db, _order_id):
        return delivery

    calls: list[str] = []

    async def _dispatch(_db, o):
        calls.append(o.order_number)
        delivery.last_error = "Courier is not configured; dispatch this order by hand"
        return delivery

    original_get, original_dispatch, original_enabled = (
        lalamove_service.get_delivery,
        lalamove_service.dispatch_order,
        lalamove_service.is_enabled,
    )
    lalamove_service.get_delivery = _get_delivery  # type: ignore[assignment]
    lalamove_service.dispatch_order = _dispatch  # type: ignore[assignment]
    lalamove_service.is_enabled = lambda: False  # type: ignore[assignment]
    try:
        result = await batching_service.assign_or_dispatch(_FakeDb(order), order)
    finally:
        lalamove_service.get_delivery = original_get  # type: ignore[assignment]
        lalamove_service.dispatch_order = original_dispatch  # type: ignore[assignment]
        lalamove_service.is_enabled = original_enabled  # type: ignore[assignment]

    assert calls == [order.order_number], "the single-order path should have run"
    assert result.batch_id is None
    assert "by hand" in (result.last_error or "")


# ── the clock ─────────────────────────────────────────────────────────────────


def test_the_event_time_comes_from_the_epoch_not_the_iso_string():
    """
    Lalamove's `updatedAt` is Gulf local time wearing a `Z`, and the top-level
    `timestamp` is the truth. Taken from production task 3554059933802783529:
    the string said `2026-08-03T21:38.00Z`, the epoch said 1785778684 — 17:38:04
    UTC — and our own clock agreed with the epoch to the second.

    Reading the string put `delivered_at` four hours in the future on every
    Lalamove order we have ever taken. It also parses only by accident: Python
    3.12 accepts `HH:MM.ss`, 3.14 raises, so the recorded value changed with the
    runtime and nothing would have failed.
    """
    real = {
        "timestamp": 1785778684,
        "data": {"updatedAt": "2026-08-03T21:38.00Z", "order": {"status": "COMPLETED"}},
    }
    assert lalamove_service.webhook_time(real) == datetime(
        2026, 8, 3, 17, 38, 4, tzinfo=timezone.utc
    )
    # And the string, whatever any Python does with it, is never consulted.
    assert (
        lalamove_service.webhook_time({"data": {"updatedAt": "2026-08-03T21:38.00Z"}})
        is None
    )


async def test_a_completion_is_stamped_when_it_happened():
    """The end-to-end version of the above, through `apply_webhook`."""
    delivery, order = (
        _delivery(courier_status="PICKED_UP"),
        _order(OrderStatusEnum.OUT_FOR_DELIVERY),
    )
    completed_at = NOW + timedelta(minutes=30)

    await lalamove_service.apply_webhook(
        _FakeDb(order), _event("COMPLETED", at=completed_at), delivery
    )

    # The moment recorded in the order's history is the courier's, to the
    # second — not ours, and not the four-hours-ahead local time their
    # `updatedAt` string carries.
    recorded = pending_events(order)
    assert [e[0] for e in recorded] == ["delivered"]
    assert recorded[0][3] == completed_at, (
        "the delivered stamp drifted — the local-time `updatedAt` is being "
        "read again in place of the epoch"
    )
