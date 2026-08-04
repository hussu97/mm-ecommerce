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
from app.services.lalamove_service import PickupPoint

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
TASK_NR = "EHG84NNJMVG35BTDE"


class _FakeResult:
    def __init__(self, value, scalar=None):
        self._value = value
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)

    def scalar(self):
        return self._scalar


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


#: 10:00 and 20:00 on the shop's clock. The second is inside the evening surge
#: window, which is where most orders land.
OFF_PEAK = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
PEAK = datetime(2026, 8, 4, 16, 0, tzinfo=timezone.utc)


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
        # And one-fifty from fifteen, not from twenty.
        (17.0, "20.00"),
        (20.0, "24.50"),
        # The card has no band past twenty; it does not keep climbing.
        (25.0, "24.50"),
        (60.0, "24.50"),
    ],
)
def test_the_bands_are_marginal(km, expected):
    assert noon_send_service.rate_card_cost(km, at=OFF_PEAK) == Decimal(expected)


@pytest.mark.parametrize("km", [3.0, 10.0, 12.0, 15.0, 20.0])
def test_the_surge_adds_a_dirham_across_every_band(km):
    off = noon_send_service.rate_card_cost(km, at=OFF_PEAK)
    peak = noon_send_service.rate_card_cost(km, at=PEAK)
    assert peak - off == Decimal("1.00")


@pytest.mark.parametrize(
    "hour_utc,surging",
    [
        (6, False),  # 10:00 — morning, quiet
        (8, True),  # 12:00 — lunch window opens
        (10, True),  # 14:00 — inside it
        (11, False),  # 15:00 — closes on the hour, half-open
        (15, True),  # 19:00 — evening window opens
        (17, True),  # 21:00 — inside it
        (18, False),  # 22:00 — closes
    ],
)
def test_the_surge_windows_are_read_on_the_shops_clock(hour_utc, surging):
    """
    Quoted as 12:00–15:00 and 19:00–22:00 local. Read as UTC they would be four
    hours out, which would put the surge in the middle of the afternoon lull and
    miss the evening entirely.
    """
    moment = datetime(2026, 8, 4, hour_utc, 0, tzinfo=timezone.utc)
    assert noon_send_service.is_surge(moment) is surging


def test_the_bulky_car_tier_is_priced_separately():
    """
    AED 25 rather than 12, and the increments are the same on top. Kept honest
    rather than absent: a large cake that has to go by car costs what it costs,
    and pricing it as a bike would hide a loss.
    """
    assert noon_send_service.rate_card_cost(5.0, bulky=True, at=OFF_PEAK) == Decimal(
        "25.00"
    )
    assert noon_send_service.rate_card_cost(15.0, bulky=True, at=OFF_PEAK) == Decimal(
        "30.00"
    )


def test_the_car_tier_never_beats_lalamove_and_the_bike_always_does():
    """
    The whole case for this courier, in one assertion. On a bike they are
    cheaper at every distance in range; in the bulky car product they are dearer
    at every distance in range — so a zone priced on the car tier would be a
    zone that should have stayed on Lalamove.
    """
    for km in (3, 5, 10, 12, 15, 20):
        lalamove = Decimal(f"{17 + 0.70 * km:.2f}")
        assert noon_send_service.rate_card_cost(km, at=PEAK) < lalamove, f"bike {km}km"
        assert (
            noon_send_service.rate_card_cost(km, bulky=True, at=OFF_PEAK) > lalamove
        ), f"car {km}km"


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
        branch_id=uuid.uuid4(),
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
            # `scalar` answers the payments SUM; `scalars().first()` answers
            # the order lookup. One stub serves both because the two calls
            # never read the same attribute.
            return _FakeResult(order, scalar=Decimal("185.00"))

        async def commit(self):
            pass

    sent: dict = {}

    async def get_delivery(*_args):
        return delivery

    async def resolve_pickup(*_args):
        return PickupPoint(
            name="Melting Moments Cakes",
            phone="+971501234567",
            address="Al Majaz 3, Sharjah",
            latitude=25.3304139,
            longitude=55.3736131,
            reference="K001",
            noon_send_outlet_code="PCKP_MLTNGM3W62",
        )

    async def create_task(**kwargs):
        sent.update(kwargs)
        return {"mp_task_nr": TASK_NR, "status": "successful"}

    async def estimate(*_args, **_kwargs):
        return None, "not under test"

    monkeypatch.setattr(noon_send_service, "get_delivery", get_delivery)
    monkeypatch.setattr(noon_send_service, "resolve_pickup", resolve_pickup)
    monkeypatch.setattr(noon_send_service.provider, "create_task", create_task)
    monkeypatch.setattr(noon_send_service, "estimate_for_point", estimate)

    result = await noon_send_service.dispatch_order(_Db(), order)
    assert result.courier_order_id == TASK_NR
    assert result.courier_status == "pending_assignment"
    assert result.courier_status in {
        s.value for s in noon_send_service.NoonSendStatusEnum
    }
    # The branch that resolved, not a global — this is what makes a second
    # kitchen a row in the admin rather than a deploy.
    assert sent["outlet_code"] == "PCKP_MLTNGM3W62"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "branch_code,setting,expected",
    [
        # The branch is the source of truth.
        ("PCKP_BRANCH01", "PCKP_GLOBAL01", "PCKP_BRANCH01"),
        # The setting is the fallback that keeps the single-kitchen deployment
        # working with nothing filled in.
        (None, "PCKP_GLOBAL01", "PCKP_GLOBAL01"),
        (None, "", ""),
    ],
)
async def test_the_outlet_code_comes_from_the_branch_first(
    monkeypatch, branch_code, setting, expected
):
    """
    Which outlet a rider collects from is a property of the place, not of the
    account. One environment variable cannot hold two answers, and the second
    kitchen is the whole reason this moved onto the branch.
    """
    from app.core.config import settings as app_settings
    from app.services import lalamove_service

    monkeypatch.setattr(app_settings, "NOON_SEND_OUTLET_CODE", setting)
    monkeypatch.setattr(app_settings, "LALAMOVE_PICKUP_BRANCH_REF", "")
    monkeypatch.setattr(app_settings, "LALAMOVE_SENDER_PHONE", "")

    branch = SimpleNamespace(
        name="Melting Moments Cakes",
        reference="K001",
        address="Al Majaz 3, Sharjah",
        latitude=Decimal("25.3304139"),
        longitude=Decimal("55.3736131"),
        phone="+971501234567",
        noon_send_outlet_code=branch_code,
        opening_from="09:00",
        opening_to="23:00",
    )

    class _Db:
        async def execute(self, _stmt):
            return _FakeResult(branch)

    pickup = await lalamove_service.resolve_pickup(_Db())
    assert pickup.noon_send_outlet_code == expected
    assert pickup.reference == "K001"


@pytest.mark.asyncio
async def test_an_unregistered_branch_is_named_in_the_refusal(monkeypatch):
    """
    The fix for this is a field in the admin, so the message has to say which
    branch — "noon Send is not configured" would send someone to the deploy
    secrets, which are fine.
    """

    async def resolve_pickup(*_args):
        return PickupPoint(
            name="Barsha Heights",
            phone="+971501234567",
            address="Barsha Heights, Dubai",
            latitude=25.0984482,
            longitude=55.1741736,
            reference="B001",
            noon_send_outlet_code="",
        )

    monkeypatch.setattr(noon_send_service, "resolve_pickup", resolve_pickup)
    order = SimpleNamespace(
        branch_id=uuid.uuid4(),
        shipping_address_snapshot={"latitude": 25.0985, "longitude": 55.1742},
    )

    allowed, reason = await noon_send_service.may_serve(_FakeDb(order), order)
    assert not allowed
    assert "B001" in reason
    assert "outlet code" in reason


def test_building_a_task_touches_only_plain_columns():
    """
    `build_task` runs synchronously inside an async dispatch, so reading a
    relationship off the order would not be a wrong number — it would be a
    `MissingGreenlet` and a failed dispatch for every order that did not happen
    to arrive with that relationship already loaded.

    This is a regression test. The first version derived the COD amount from
    `order.amount_paid`, which walks `order.payments`; it survived every unit
    test and blew up the first time a real order was dispatched. The order stub
    below has no `payments`, `amount_paid` or `balance_due` at all, so touching
    one raises rather than silently working.
    """
    order = SimpleNamespace(
        order_number="MM-1001",
        total=Decimal("185.00"),
        payment_method="cod",
        notes=None,
        shipping_address_snapshot={
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Al Majaz Waterfront, Al Majaz 3",
            "unit_number": "1",
            "city": "Sharjah",
        },
    )

    task, reason = noon_send_service.build_task(order, Decimal("185.00"))

    assert reason is None
    assert task.cod_value == 18500
    assert task.prepaid_value == 0
    assert task.drop_off_address["lat"] == 253213000
    assert task.drop_off_address["lng"] == 553820000


def test_a_paid_order_is_sent_as_prepaid_even_when_flagged_cod():
    """Nothing left to collect means nothing for the rider to collect."""
    order = SimpleNamespace(
        order_number="MM-1002",
        total=Decimal("185.00"),
        payment_method="cod",
        notes=None,
        shipping_address_snapshot={
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Al Majaz Waterfront, Al Majaz 3",
            "city": "Sharjah",
        },
    )

    task, _ = noon_send_service.build_task(order, Decimal("0.00"))

    assert task.cod_value == 0
    assert task.prepaid_value == 18500


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
