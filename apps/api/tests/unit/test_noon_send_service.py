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
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect

from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.order_driver import OrderDriver
from app.models.order_status_event import pending_events
from app.services import driver_assignment, noon_send_service
from app.services.lalamove_service import PickupPoint
from app.services.providers import noon_send_provider

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
TASK_NR = "EHG84NNJMVG35BTDE"
#: The short number that goes out as `order_reference` — seven digits a
#: rider can read back, in place of `MM-20260804-014`.
REFERENCE = "4820193"


class _FakeResult:
    def __init__(self, value, scalar=None):
        self._value = value
        self._scalar = scalar

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)

    def scalar(self):
        return self._scalar


class _FakeDb:
    """
    A session for the two things these tests read through one.

    It has to tell the queries apart. `apply_webhook` looks up the `Order`;
    `driver_assignment` looks up the active `OrderDriver` row and inserts new
    ones — and a fake that answered both with the same object would hand an
    `Order` back as somebody's driver and make every reassignment test agree
    with itself for the wrong reason.
    """

    def __init__(self, order=None, drivers=None):
        self.order = order
        self.drivers: list[OrderDriver] = list(drivers or [])
        self.pending: list[OrderDriver] = []

    def add(self, row):
        # Pending until flushed, because the real sessionmaker is
        # `autoflush=False` and `driver_assignment` depends on knowing it.
        self.pending.append(row)

    async def get(self, model, pk):
        """Primary-key load, which is how `route_now` resolves the order.

        It reads the order by id rather than through `delivery.order`: an
        unloaded relationship raises under asyncpg instead of reading as
        absent, so the attribute was never safe to touch. `get` consults the
        identity map first, which is what this stands in for.
        """
        if model is Order:
            return self.order
        return None

    async def flush(self):
        self.drivers.extend(self.pending)
        self.pending.clear()

    async def execute(self, stmt):
        if _selects(stmt, OrderDriver):
            active = next((d for d in self.drivers if d.is_active), None)
            return _FakeResult(active)
        return _FakeResult(self.order)


def _selects(stmt, model) -> bool:
    """Whether this SELECT is asking for *model*."""
    return any(
        description.get("entity") is model
        for description in getattr(stmt, "column_descriptions", [])
    )


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
    # Pinned off-peak so the curve under test is the same one every run. The
    # surge is a constant and would not break monotonicity, but a test that
    # quietly measures a different curve depending on the hour is a test whose
    # failures nobody can reproduce.
    off_peak = datetime(2026, 8, 4, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    previous = Decimal("0")
    step = 0.25
    km = 0.0
    while km <= 40:
        cost = noon_send_service.rate_card_cost(km, at=off_peak)
        assert cost >= previous, f"{km} km costs less than the distance before it"
        previous = cost
        km += step


def test_distance_is_scaled_from_straight_line_by_the_measured_detour():
    """Muwaileh: 8.54 km straight line, 12.8 km by road on the live rate card."""
    km = noon_send_service.road_distance_km(25.3304139, 55.3736131, 25.3120, 55.4560)
    assert 12.0 <= km <= 13.5


# ── one push at a time ────────────────────────────────────────────────────────


def _order(status: OrderStatusEnum) -> Order:
    """A real `Order`, so the status listener fires.

    The moments a courier reports are recorded in `order_status_events` rather
    than in columns on `order_deliveries`, and the listener only sees an
    assignment on an actual mapped instance — a `SimpleNamespace` would take the
    write silently and record nothing. Starts with an empty history: building it
    at a status is a transition too, and these tests are about the next one.
    """
    order = Order(id=uuid.uuid4(), status=status, order_number="MM-NS-1")
    inspect(order).info.pop("pending_status_events", None)
    return order


@pytest.mark.asyncio
async def test_collected_sends_the_order_out_for_delivery():
    order = _order(OrderStatusEnum.PACKED)
    delivery = _delivery()
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("picked_up", NOW + timedelta(minutes=5)), delivery
    )
    assert delivery.courier_status == "picked_up"
    assert order.status == OrderStatusEnum.OUT_FOR_DELIVERY
    # The collection moment lives in the order's history now, not in a column
    # on `order_deliveries` — and it is theirs, not ours.
    assert pending_events(order) and pending_events(order)[0][0] == "out_for_delivery"
    assert pending_events(order)[0][3] == NOW + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_delivered_closes_the_order():
    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("delivered", NOW + timedelta(minutes=30)), delivery
    )
    assert order.status == OrderStatusEnum.DELIVERED
    assert pending_events(order)[0][0] == "delivered"
    assert pending_events(order)[0][3] == NOW + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_undelivered_moves_the_order_and_flags_a_human_without_refunding(
    monkeypatch,
):
    """
    A rider who could not hand the parcel over is a problem for the shop, not a
    cancellation for the customer — but it is not `out_for_delivery` either.

    It used to be. The order stayed where it was and only the delivery row knew,
    so MM-20260805-006 read "on the way" for hours after noon Send reported that
    nobody was bringing it. The status moves now; the flag stays, because
    somebody still has to decide what the shop says to the customer.

    **And it does not refund.** For a while it did, from here — a rider's push
    paid the customer back automatically, which quietly made every failed
    handover a write-off: the money was gone before anybody had looked at the
    cake still sitting on the shelf. A courier's status push is not a decision
    to stop selling somebody a cake. The flag is what asks a person to make
    that decision, and cancelling is where they make it.
    """
    from app.services import payment_service

    refunded: list[Order] = []

    async def _record_refund(db, order):
        refunded.append(order)
        return Decimal("0")

    monkeypatch.setattr(payment_service, "refund_order", _record_refund)

    order = _order(OrderStatusEnum.OUT_FOR_DELIVERY)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("undelivered", NOW + timedelta(hours=1)), delivery
    )
    assert order.status == OrderStatusEnum.UNDELIVERED
    assert delivery.needs_attention
    assert "re-dispatch" in delivery.last_error
    assert refunded == [], "a rider's push must not hand the money back"


@pytest.mark.asyncio
async def test_a_parcel_nobody_collected_cannot_come_back_undelivered():
    """Only a rider who actually took the box can fail to hand it over."""
    order = _order(OrderStatusEnum.CONFIRMED)
    delivery = _delivery(courier_status="assigned")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("undelivered", NOW + timedelta(hours=1)), delivery
    )
    assert order.status == OrderStatusEnum.CONFIRMED
    assert not pending_events(order)


@pytest.mark.asyncio
async def test_a_second_rider_cannot_reopen_an_undelivered_order():
    """
    `undelivered` is an ending, not a detour — what follows is a refund
    conversation, not another van.

    This test used to assert the opposite: this service kept a private
    allowance letting a re-dispatched rider's `picked_up` pull the order back
    to `out_for_delivery`, written before the shop decided undelivered orders
    are written off (and months after the map had said so — the two rulebooks
    had drifted). With the refund now firing the moment `undelivered` lands,
    reopening the journey afterwards would send a second rider out with a cake
    the customer is being repaid for. One rulebook: the courier's push is
    refused, the parcel stays written off, and re-serving the customer is a
    new order.
    """
    order = _order(OrderStatusEnum.UNDELIVERED)
    delivery = _delivery(courier_status="assigned")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("picked_up", NOW + timedelta(hours=2)), delivery
    )
    assert order.status == OrderStatusEnum.UNDELIVERED
    assert not pending_events(order)


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
    order = _order(OrderStatusEnum.REFUNDED)
    delivery = _delivery(courier_status="picked_up")
    await noon_send_service.apply_webhook(
        _FakeDb(order), _push("delivered", NOW + timedelta(hours=2)), delivery
    )
    assert order.status == OrderStatusEnum.REFUNDED
    assert not pending_events(order)


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
    "branch_code,expected", [("PCKP_BRANCH01", "PCKP_BRANCH01"), (None, "")]
)
async def test_the_outlet_code_comes_from_the_branch_and_nowhere_else(
    branch_code, expected
):
    """
    Which outlet a rider collects from is a property of the place.

    There used to be a `NOON_SEND_OUTLET_CODE` setting behind this as a
    fallback, which meant one environment variable was answering a question that
    has one answer per kitchen — and the moment a second kitchen exists it is
    wrong for one of them, silently, by dispatching from the other one's door.
    A branch with no code simply cannot dispatch through noon Send, which is a
    refusal an admin can fix in the field it belongs to.
    """
    from app.services import lalamove_service

    branch = SimpleNamespace(
        name="Melting Moments Cakes",
        reference="K001",
        address="Al Majaz 3, Sharjah",
        city="Sharjah",
        latitude=Decimal("25.3304139"),
        longitude=Decimal("55.3736131"),
        phone="+971501234567",
        noon_send_outlet_code=branch_code,
        noon_send_outlet_address_code=None,
        opening_from="09:00",
        opening_to="23:00",
    )

    class _Db:
        async def execute(self, _stmt):
            return _FakeResult(branch)

    pickup = await lalamove_service.resolve_pickup(_Db())
    assert pickup.noon_send_outlet_code == expected
    assert pickup.reference == "K001"
    # The driver's number is the branch's, with nothing able to override it.
    assert pickup.phone == "+971501234567"
    # And the emirate, which Slider's vehicle rule turns on — a bike may not
    # cross a boundary, so "which emirate is the kitchen in" has to travel with
    # the pickup rather than be re-derived from the address line.
    assert pickup.emirate == "Sharjah"


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

    task, reason = noon_send_service.build_task(order, Decimal("185.00"), REFERENCE)

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

    task, _ = noon_send_service.build_task(order, Decimal("0.00"), REFERENCE)

    assert task.cod_value == 0
    assert task.prepaid_value == 18500


@pytest.mark.asyncio
async def test_rider_position_overwrites_rather_than_accumulates():
    """
    Each ping replaces the last — there is no breadcrumb trail, only where the
    rider is now.

    Written originally against an invented payload: `da_details.latitude`, in
    plain degrees. Both halves of that were wrong, and because the test agreed
    with the code it made the shape look verified. It is noon's real one now —
    nested under `location`, degrees times 10^7, as strings.
    """
    delivery = _delivery()
    await noon_send_service.apply_tracking(
        _FakeDb(),
        delivery,
        {
            "da_details": {
                "location": {"latitude": "253300000", "longitude": "553700000"}
            }
        },
    )
    await noon_send_service.apply_tracking(
        _FakeDb(),
        delivery,
        {
            "da_details": {
                "location": {"latitude": "253100000", "longitude": "554000000"}
            }
        },
    )
    assert delivery.driver_latitude == Decimal("25.31")
    assert delivery.driver_longitude == Decimal("55.40")


@pytest.mark.asyncio
async def test_a_tracking_push_naming_a_different_rider_is_a_reassignment():
    """
    Their status webhook has no rider in it at all, and a swap does not always
    change the status — a task can be handed on while it sits at `assigned`.
    These pings are then the *only* thing naming the new person: a name and a
    number arriving every fifteen seconds that nothing was reading.
    """
    db = _FakeDb(order=None)
    delivery = _delivery()
    await noon_send_service.apply_tracking(
        db,
        delivery,
        {
            "da_details": {
                "name": "Mohammed",
                "phone_number": "+971500000003",
                "location": {"latitude": "253300000", "longitude": "553700000"},
            }
        },
    )

    change = await noon_send_service.apply_tracking(
        db,
        delivery,
        {
            "da_details": {
                "name": "Bilal",
                "phone_number": "+971500000004",
                "location": {"latitude": "253100000", "longitude": "554000000"},
            }
        },
    )

    assert change is driver_assignment.Change.REASSIGNED
    assert delivery.driver_name == "Bilal"
    assert delivery.driver_phone == "+971500000004"
    assert delivery.driver_assignment_count == 2
    # The new rider's own pin, not the one the old rider left behind.
    assert delivery.driver_latitude == Decimal("25.31")
    assert len([d for d in db.drivers if d.is_active]) == 1


@pytest.mark.asyncio
async def test_a_repeated_assigned_still_asks_who_is_carrying_it(monkeypatch):
    """
    A swap arrives as the word `assigned` a second time.

    This used to be read as "no transition" — `status != delivery.courier_status`
    was false — so the detail was never re-read and the second rider was never
    learned. The rank guard lets an equal status through precisely so that this
    can be noticed.
    """
    asked: list[str] = []

    async def _get_task(task_nr):
        asked.append(task_nr)
        return {
            "status_code": "assigned",
            "da_details": {"name": "Bilal", "phone_number": "+971500000004"},
        }

    monkeypatch.setattr(noon_send_service, "is_enabled", lambda: True)
    monkeypatch.setattr(noon_send_provider.provider, "get_task", _get_task)
    monkeypatch.setattr(noon_send_service, "_announce_rider", _swallow_announcement)

    order = _order(OrderStatusEnum.PACKED)
    delivery = _delivery(
        courier_status="assigned",
        driver_name="Mohammed",
        driver_phone="+971500000003",
        driver_assignment_count=1,
    )
    await noon_send_service.apply_webhook(_FakeDb(order), _push("assigned"), delivery)

    assert asked == [TASK_NR]
    assert delivery.driver_name == "Bilal"
    assert delivery.driver_assignment_count == 2


async def _swallow_announcement(_db, _delivery):
    """The push is somebody else's test; this one is about who the row names."""


@pytest.mark.asyncio
async def test_a_position_push_without_coordinates_is_ignored():
    delivery = _delivery(driver_latitude=Decimal("25.33"))
    await noon_send_service.apply_tracking(_FakeDb(), delivery, {"da_details": {}})
    assert delivery.driver_latitude == Decimal("25.33")


def test_the_distance_guard_is_not_tighter_than_the_zone_we_drew():
    """
    Our own pre-check must not refuse addresses the map hands to noon Send.

    `Sharjah Central` is a 13.4 km circle because that is 20 road km over the
    measured detour factor, and 20 km is where noon Send's rate card stops. If
    `NOON_SEND_MAX_DISTANCE_M` is set below that, every pin in the outer ring of
    the zone is refused here — before noon Send is ever asked — and falls back to
    Lalamove. Nothing breaks and nobody is overcharged, which is exactly why it
    would go unnoticed: the zone simply stops doing the thing it was drawn for.

    This shipped once, at 15000 against a 20 km zone, and would have excluded Al
    Zahia and University City — the two areas the redraw was for.
    """
    from app.core.config import settings

    zone_radius_km = 13.4
    reachable_km = zone_radius_km * settings.NOON_SEND_DETOUR_FACTOR
    guard_km = settings.NOON_SEND_MAX_DISTANCE_M / 1000

    assert guard_km >= reachable_km, (
        f"the guard stops at {guard_km:.1f} km but the zone reaches "
        f"{reachable_km:.1f} road km"
    )
    # And not looser than the card can price, which would book a run at a fee
    # the rate card has no band for.
    assert guard_km <= noon_send_service.RATE_CARD_MAX_KM


# ── where a rider collects ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_order_collects_from_its_own_branch(monkeypatch):
    """
    One function answers this, so the serviceability check, the task and the
    cost estimate cannot disagree — a run quoted from Sharjah and booked from
    Dubai would be wrong by thirty kilometres.

    There was briefly a second answer. While the integration was proved against
    noon's staging fleet, one named account collected from a fixed Dubai outlet,
    because that fleet serves three Dubai outlets and cannot cross an emirate
    boundary. Nothing is special now: the branch making the order is the branch
    a rider goes to.
    """
    branch_pickup = PickupPoint(
        name="Melting Moments Cakes",
        phone="+971501234567",
        address="Al Majaz 3, Sharjah",
        latitude=25.3304139,
        longitude=55.3736131,
        reference="K001",
        noon_send_outlet_code="MLTNGM1GBF",
    )

    async def resolve(_db, _branch_id=None):
        return branch_pickup

    monkeypatch.setattr(noon_send_service, "resolve_pickup", resolve)

    for email in ("was-the-trial-account@example.com", "someone@else.com"):
        order = SimpleNamespace(user_id=uuid.uuid4(), email=email, branch_id=None)
        pickup = await noon_send_service.pickup_for(None, order)
        assert pickup.noon_send_outlet_code == "MLTNGM1GBF"


# ── a fully discounted order ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "total,subtotal,expected",
    [
        # The ordinary case: what they paid.
        (Decimal("185.00"), Decimal("170.00"), 18500),
        # A 100% promo. Nothing was paid, so the flat stand-in applies.
        (Decimal("0.00"), Decimal("50.00"), 100),
        (Decimal("0.00"), Decimal("0.00"), 100),
    ],
)
def test_a_prepaid_task_always_carries_a_value(total, subtotal, expected):
    """
    noon Send requires one of `cod_value` and `prepaid_value` and treats zero as
    absent — `cod_prepaid_missing`, a 400 at task creation.

    Production found it the honest way: a trial order with a 100% promo code had
    a total of AED 0.00, sent `prepaid_value: 0`, was refused, and fell back to
    Lalamove. AED 1.00 stands in — flat, because these are staging tasks that
    never reach a rider and a derived figure would put money in noon's records
    that nobody paid. Nothing here can cause any to be collected: only
    `cod_value` does that, and it stays zero throughout.
    """
    order = SimpleNamespace(
        order_number="MM-20260805-003",
        total=total,
        subtotal=subtotal,
        payment_method="stripe",
        notes=None,
        shipping_address_snapshot={
            "latitude": 25.1437,
            "longitude": 55.2857,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Boulevard Plaza Tower 2, Downtown Dubai",
            "unit_number": "1",
            "city": "Dubai",
        },
    )

    task, reason = noon_send_service.build_task(order, Decimal("0.00"), REFERENCE)

    assert reason is None
    assert task.prepaid_value == expected
    assert task.cod_value == 0, "a prepaid task must never ask a rider to collect"


def test_a_cash_order_is_unaffected():
    """COD already refuses to be zero — `is_cod` requires an outstanding balance."""
    order = SimpleNamespace(
        order_number="MM-20260805-009",
        total=Decimal("85.00"),
        subtotal=Decimal("70.00"),
        payment_method="cod",
        notes=None,
        shipping_address_snapshot={
            "latitude": 25.1437,
            "longitude": 55.2857,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Boulevard Plaza Tower 2, Downtown Dubai",
            "city": "Dubai",
        },
    )

    task, _ = noon_send_service.build_task(order, Decimal("85.00"), REFERENCE)

    assert task.cod_value == 8500
    assert task.prepaid_value == 0


# ── where the rider is ────────────────────────────────────────────────────────

#: A real tracking payload, taken verbatim from staging task HG85NNE6X31CRNRQ
#: while rider Umang Goel was 9.2 km from the drop-off.
REAL_TRACKING = {
    "order_nr": "HG85NNE6X31CRNRQ",
    "order_reference": "MM-20260805-006",
    "da_details": {
        "name": "Umang Goel",
        "phone_number": "+91-9898785897",
        "mot_code": "motorbike",
        "location": {"latitude": "252017557", "longitude": "552733762"},
        "proximity": {"drop_off": {"distance": 9279.6, "duration": 612.6}},
    },
}


@pytest.mark.asyncio
async def test_a_tracking_push_moves_the_rider_pin():
    """
    The coordinates are nested under `da_details.location`, and encoded the way
    we encode ours — degrees times 10^7, as strings.

    This used to read `da_details.latitude`, one level too high and
    unconverted. The lookup missed, the guard never fired, and every push was a
    silent no-op — nothing written, nothing logged. Two real ones arrived that
    way before anyone thought to look at the column.
    """
    delivery = OrderDelivery(order_id=uuid.uuid4(), provider="noon_send")

    await noon_send_service.apply_tracking(_FakeDb(), delivery, REAL_TRACKING)

    assert delivery.driver_latitude == Decimal("25.2017557")
    assert delivery.driver_longitude == Decimal("55.2733762")


@pytest.mark.asyncio
async def test_the_pin_fits_the_column_it_is_written_to():
    """
    `driver_latitude` is `Numeric(9, 6)`, which stops at 999.999999. The raw
    252017557 does not fit, so the missing conversion was not merely wrong — it
    would have raised a numeric field overflow the first time anybody pressed
    "Check status" on a task with a rider on it.
    """
    delivery = OrderDelivery(order_id=uuid.uuid4(), provider="noon_send")
    await noon_send_service.apply_tracking(_FakeDb(), delivery, REAL_TRACKING)

    for value in (delivery.driver_latitude, delivery.driver_longitude):
        assert abs(value) < 1000
        assert -90 <= float(delivery.driver_latitude) <= 90
        assert -180 <= float(delivery.driver_longitude) <= 180


@pytest.mark.asyncio
async def test_a_push_with_no_rider_yet_leaves_the_pin_alone():
    """Before assignment `da_details` is null, and that is not an error."""
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="noon_send",
        driver_latitude=Decimal("25.1"),
        driver_longitude=Decimal("55.2"),
    )

    await noon_send_service.apply_tracking(
        _FakeDb(), delivery, {"order_nr": "X", "da_details": None}
    )

    assert delivery.driver_latitude == Decimal("25.1")


#: A real tracking push, verbatim from the webhook noon sent for order
#: MM-20260805-007 at 07:57:16 — flat, and already in degrees.
REAL_TRACKING_WEBHOOK = {
    "order_nr": "HG85NNJRJYC4A7EI",
    "order_reference": "MM-20260805-007",
    "da_details": {"latitude": 25.2017569, "longitude": 55.2733758},
    "timestamp": "2026-08-05 07:57:16",
}


@pytest.mark.asyncio
async def test_both_shapes_of_coordinate_are_understood():
    """
    noon sends the same number two ways, and we have both from one order.

    `GET /tasks/{nr}` nests and scales — `location.latitude == "252017557"`.
    The tracking webhook sends flat degrees — `latitude == 25.2017569`. Reading
    only the first meant thirteen tracking pushes in eight minutes moved
    nothing, while the log cheerfully said `matched: True` each time.

    Told apart by magnitude, because that is a property of the number: no
    latitude exceeds 90 and no longitude exceeds 180.
    """
    from_webhook = OrderDelivery(order_id=uuid.uuid4(), provider="noon_send")
    await noon_send_service.apply_tracking(
        _FakeDb(), from_webhook, REAL_TRACKING_WEBHOOK
    )

    from_detail = OrderDelivery(order_id=uuid.uuid4(), provider="noon_send")
    await noon_send_service.apply_tracking(_FakeDb(), from_detail, REAL_TRACKING)

    for delivery in (from_webhook, from_detail):
        assert 25.0 < float(delivery.driver_latitude) < 25.5, "not a Dubai latitude"
        assert 55.0 < float(delivery.driver_longitude) < 55.6

    # The same rider, seconds apart, from two different endpoints.
    assert abs(from_webhook.driver_latitude - from_detail.driver_latitude) < Decimal(
        "0.001"
    )


def test_delivery_notes_carry_the_customers_note_and_nothing_else():
    """
    A rider opening the task should read what the customer asked for first.

    It used to read `Order 1234567 · Ref MM-1003 · Unit 1204 · <the note>`.
    Every one of those three is already in the payload — the reference *is*
    `order_reference`, and the unit is the first thing in the address string —
    so the only new information sat behind sixty characters of duplication, in
    a field noon truncates at 250.
    """
    order = SimpleNamespace(
        order_number="MM-1003",
        total=Decimal("120.00"),
        payment_method="stripe",
        notes="  Gate code 4412, please call on arrival  ",
        shipping_address_snapshot={
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "address_line_1": "Al Majaz Waterfront, Al Majaz 3",
            "unit_number": "1204",
            "city": "Sharjah",
        },
    )

    task, reason = noon_send_service.build_task(order, Decimal("0.00"), REFERENCE)

    assert reason is None
    assert task.delivery_notes == "Gate code 4412, please call on arrival"
    # The unit is still reachable — it leads the address the rider navigates to.
    assert task.drop_off_address["address"].startswith("1204, ")


def test_no_note_means_no_notes():
    """An order without a note sends an empty field rather than an order number
    dressed up as instructions."""
    order = SimpleNamespace(
        order_number="MM-1004",
        total=Decimal("120.00"),
        payment_method="stripe",
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

    task, _ = noon_send_service.build_task(order, Decimal("0.00"), REFERENCE)

    assert task.delivery_notes == ""
