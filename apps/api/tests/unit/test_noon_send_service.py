"""
The noon Send rate card, and what one of their pushes may do to an order.

The rate card matters more here than it would for Lalamove, because noon Send
has no quotation API: this arithmetic is not a prediction that gets corrected by
an invoice later, it is the only cost figure this order will ever carry. If the
bands are read wrong, every margin number in the admin is wrong with them.

The webhook half mirrors `test_lalamove_service`: their pushes carry no event id
and no ordering guarantee, so a late update must not rewind a delivered order
and a replay must not be applied twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.order import OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services import noon_send_service

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
TASK_NR = "EHG84NNJMVG35BTDE"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)


class _FakeDb:
    def __init__(self, order):
        self.order = order

    async def execute(self, _stmt):
        return _FakeResult(self.order)


def _delivery(**overrides) -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="noon_send",
        zone_name="Sharjah Central",
        fee_charged=Decimal("15.00"),
        courier_order_id=TASK_NR,
        courier_status="assigned",
        status_updated_at=NOW,
    )
    for key, value in overrides.items():
        setattr(delivery, key, value)
    return delivery


def _push(status: str, at: datetime = NOW) -> dict:
    return {
        "order_nr": TASK_NR,
        "status_code": status,
        "order_reference": "MM-1001",
        "timestamp": at.isoformat(),
    }


# ── the rate card ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "km,expected",
    [
        # AED 12 covers the first ten kilometres outright.
        (0.0, "12.00"),
        (5.0, "12.00"),
        (9.99, "12.00"),
        (10.0, "12.00"),
        # Then a dirham a kilometre, on the distance inside the band only.
        (10.5, "12.50"),
        (12.0, "14.00"),
        (15.0, "17.00"),
        (20.0, "22.00"),
        # Then one-fifty, still marginal.
        (25.0, "29.50"),
        (30.0, "37.00"),
    ],
)
def test_the_bands_are_marginal(km, expected):
    assert noon_send_service.rate_card_cost(km) == Decimal(expected)


def test_the_card_never_charges_less_for_going_further():
    """
    Read as "pick a rate by band and apply it to the whole trip", the card would
    price eleven kilometres at AED 11 and ten at AED 12. No courier means that,
    and the monotonic reading is the one implemented — this is the test that
    would catch the other one being introduced.
    """
    previous = Decimal("0")
    step = 0.25
    km = 0.0
    while km <= 40:
        cost = noon_send_service.rate_card_cost(km)
        assert cost >= previous, f"{km} km costs less than the distance before it"
        previous = cost
        km += step


def test_it_is_cheaper_than_lalamove_everywhere_it_can_reach():
    """
    The reason the zone exists. Lalamove is `17 + 0.70/km` once the AED 5
    door-to-door is dropped; the two cross at 31.25 km, which is well past
    noon Send's own 15 km cap — so inside their reach they always win.
    """
    for km in (1, 5, 10, 12, 15):
        assert noon_send_service.rate_card_cost(km) < Decimal(
            f"{17 + 0.70 * km:.2f}"
        ), f"{km} km"


def test_distance_is_scaled_from_straight_line_by_the_measured_detour():
    """Muwaileh: 8.54 km straight line, 12.8 km by road on the live rate card."""
    km = noon_send_service.road_distance_km(25.3304139, 55.3736131, 25.3120, 55.4560)
    assert 12.0 <= km <= 13.5


# ── one push at a time ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collected_sends_the_order_out_for_delivery():
    order = SimpleNamespace(id=uuid.uuid4(), status=OrderStatusEnum.PACKED)
    delivery = _delivery()
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("picked_up", NOW + timedelta(minutes=5)), delivery
    )
    assert delivery.courier_status == "picked_up"
    assert delivery.picked_up_at == NOW + timedelta(minutes=5)
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY


@pytest.mark.asyncio
async def test_delivered_closes_the_order():
    order = SimpleNamespace(id=uuid.uuid4(), status=OrderStatusEnum.OUT_FOR_DELIVERY)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("delivered", NOW + timedelta(minutes=30)), delivery
    )
    assert order.status == OrderStatusEnum.DELIVERED
    assert delivery.delivered_at == NOW + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_undelivered_flags_a_human_and_leaves_the_order_alone():
    """
    A rider who could not hand the parcel over is a problem for the shop, not a
    cancellation for the customer: the order stays where it is so somebody can
    re-dispatch it.
    """
    order = SimpleNamespace(id=uuid.uuid4(), status=OrderStatusEnum.OUT_FOR_DELIVERY)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("undelivered", NOW + timedelta(hours=1)), delivery
    )
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY
    assert delivery.needs_attention
    assert "re-dispatch" in delivery.last_error


@pytest.mark.asyncio
async def test_a_late_push_cannot_rewind_a_delivered_order():
    order = SimpleNamespace(id=uuid.uuid4(), status=OrderStatusEnum.DELIVERED)
    delivery = _delivery(
        courier_status="delivered", status_updated_at=NOW + timedelta(minutes=30)
    )
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("assigned", NOW), delivery
    )
    assert delivery.courier_status == "delivered"
    assert order.status == OrderStatusEnum.DELIVERED


@pytest.mark.asyncio
async def test_a_settled_order_is_not_reopened():
    """A push arriving after a refund must not mark the order delivered."""
    order = SimpleNamespace(id=uuid.uuid4(), status=OrderStatusEnum.REFUNDED)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("delivered", NOW + timedelta(hours=2)), delivery
    )
    assert order.status == OrderStatusEnum.REFUNDED


def test_a_replay_produces_the_same_dedup_key():
    """
    They send no event id, so dedup keys on task, status and time. A genuine
    retry reproduces all three; a real transition changes at least one.
    """
    first = noon_send_service._event_id(_push("picked_up"))
    replay = noon_send_service._event_id(_push("picked_up"))
    later = noon_send_service._event_id(_push("delivered"))
    assert first == replay
    assert first != later


@pytest.mark.asyncio
async def test_the_ack_from_create_task_is_not_stored_as_a_status(monkeypatch):
    """
    `create-task` answers `{"mp_task_nr": ..., "status": "successful"}` — an
    acknowledgement, not a lifecycle state. Confirmed against staging: the
    task's real opening status is `pending_assignment`. Storing "successful"
    would put a word in `courier_status` that no status map has heard of, so
    the order would never advance and no failure would ever be flagged.
    """
    order = SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-1001",
        status=OrderStatusEnum.PACKED,
        total=Decimal("185.00"),
        amount_paid=Decimal("185.00"),
        payment_method="stripe",
        notes=None,
        shipping_address_snapshot={
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Al Majaz Waterfront",
            "city": "Sharjah",
        },
    )
    delivery = _delivery(courier_order_id=None, courier_status=None)

    class _Db:
        async def execute(self, _stmt):
            return _FakeResult(order)

        async def commit(self):
            pass

    async def get_delivery(*_args):
        return delivery

    async def create_task(**_kwargs):
        return {"mp_task_nr": TASK_NR, "status": "successful"}

    async def estimate(*_args, **_kwargs):
        return None, "not under test"

    monkeypatch.setattr(noon_send_service, "get_delivery", get_delivery)
    monkeypatch.setattr(noon_send_service.provider, "create_task", create_task)
    monkeypatch.setattr(noon_send_service, "estimate_for_point", estimate)

    result = await noon_send_service.dispatch_order(_Db(), order)
    assert result.courier_order_id == TASK_NR
    assert result.courier_status == "pending_assignment"
    assert result.courier_status in {
        s.value for s in noon_send_service.NoonSendStatusEnum
    }


def test_rider_position_overwrites_rather_than_accumulates():
    delivery = _delivery()
    noon_send_service.apply_tracking(
        delivery, {"da_details": {"latitude": 25.33, "longitude": 55.37}}
    )
    noon_send_service.apply_tracking(
        delivery, {"da_details": {"latitude": 25.31, "longitude": 55.40}}
    )
    assert delivery.driver_latitude == Decimal("25.31")
    assert delivery.driver_longitude == Decimal("55.40")


def test_a_position_push_without_coordinates_is_ignored():
    delivery = _delivery(driver_latitude=Decimal("25.33"))
    noon_send_service.apply_tracking(delivery, {"da_details": {}})
    assert delivery.driver_latitude == Decimal("25.33")
