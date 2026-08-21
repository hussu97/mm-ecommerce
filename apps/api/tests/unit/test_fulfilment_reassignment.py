"""
Moving one order to a different courier: who may, who may not, and in what order.

Three groups, and the middle one is the reason the module exists.

**Where an order may go** is the map's answer, not the code's. A zone names a
preferred courier and the alternates its orders may be moved to, so the tests
about Dubai and noon Send are tests about a row rather than about an `if`.

**Whether it may move at all** is the denial ladder in `refuse`. Each rung has a
test, because each of them is a sentence somebody reads on a screen at the point
they are trying to rescue a paid order, and a wrong one sends them to a phone.

**What moving actually does** is the ordering: call the old courier off, leave
the run, close the driver's stint, and only then book. Booking first would
overwrite the one column holding the old booking's id while that booking is
still alive — which is how two drivers get sent to one cake.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictError
from app.models.delivery_polygon import DeliveryPolygon
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services import fulfilment_reassignment as reassign

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
LALAMOVE = "lalamove"
NOON_SEND = "noon_send"
THIRD_PARTY = "third_party"


class _Db:
    """
    Enough session for the reads this module makes, and nothing more.

    `db.get` answers the polygon lookup; `execute` answers the locking select
    for the delivery row. Both are scripted rather than queried because none of
    the rules under test are about SQL.

    Models `autoflush=False` the way the real sessionmaker is built — `add`
    holds a row pending and only `flush` publishes it. Nothing here reads back
    what it added, but a fake that is read-your-own-writes when the application
    is not is a fake that asserts a database we do not have, and the last one
    that did hid a production bug for ten green tests.
    """

    def __init__(self, delivery=None, polygon=None):
        self.delivery = delivery
        self.polygon = polygon
        self.pending: list = []
        self.rows: list = []
        self.locked = False

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        self.rows.extend(self.pending)
        self.pending.clear()

    async def get(self, model, pk):
        return self.polygon if model is DeliveryPolygon else None

    async def execute(self, stmt):
        if getattr(stmt, "_for_update_arg", None) is not None:
            self.locked = True
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: self.delivery)
        )

    async def commit(self):
        pass


def _order(status=OrderStatusEnum.PACKED, **overrides) -> Order:
    order = Order(
        id=uuid.uuid4(),
        order_number="MM-RA-1",
        status=status,
        delivery_method=DeliveryMethodEnum.DELIVERY,
    )
    order.refunded_amount = Decimal("0")
    order.shipping_address_snapshot = {"latitude": 25.2, "longitude": 55.3}
    for key, value in overrides.items():
        setattr(order, key, value)
    return order


def _delivery(**overrides) -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider=THIRD_PARTY,
        zone_name="Dubai Mid",
        fee_charged=Decimal("30.00"),
    )
    delivery.polygon_id = uuid.uuid4()
    delivery.previous_courier_order_ids = []
    delivery.driver_assignment_count = 0
    for key, value in overrides.items():
        setattr(delivery, key, value)
    return delivery


def _polygon(preferred=LALAMOVE, alternates=("third_party",)) -> DeliveryPolygon:
    polygon = DeliveryPolygon(
        name="Dubai Mid",
        delivery_fee=Decimal("30.00"),
        geometry={},
        min_lat=24,
        max_lat=26,
        min_lng=54,
        max_lng=57,
    )
    polygon.fulfilment_provider = preferred
    polygon.alternate_providers = list(alternates)
    return polygon


# ── where an order may go ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_zone_decides_and_the_current_courier_is_not_an_option():
    """Moving an order to where it already is is not a move."""
    delivery = _delivery(provider=THIRD_PARTY)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    preferred, allowed = await reassign.allowed_targets(db, _order(), delivery)
    assert preferred == LALAMOVE
    assert allowed == (LALAMOVE,)


@pytest.mark.asyncio
async def test_a_dubai_order_cannot_be_handed_to_noon_send():
    """
    The rule the shop asked for. noon Send cannot cross an emirate boundary and
    will not carry a run past 20 km, so a zone given to Lalamove is a zone they
    probably cannot reach — and offering them anyway buys a refusal at best.
    """
    delivery = _delivery(provider=LALAMOVE)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    _, allowed = await reassign.allowed_targets(db, _order(), delivery)
    assert allowed == (THIRD_PARTY,)
    assert NOON_SEND not in allowed


@pytest.mark.asyncio
async def test_a_noon_send_zone_may_go_either_way():
    delivery = _delivery(provider=NOON_SEND)
    db = _Db(delivery, _polygon(NOON_SEND, ["third_party", "lalamove"]))
    _, allowed = await reassign.allowed_targets(db, _order(), delivery)
    assert set(allowed) == {THIRD_PARTY, LALAMOVE}


@pytest.mark.asyncio
async def test_an_order_can_be_moved_back_to_its_zones_own_courier():
    """
    The return leg, and the reason the preferred courier is in the allowed set
    rather than only its alternates. A Dubai order parked with a third party
    while Lalamove was quiet goes back with the same button, no special case.
    """
    delivery = _delivery(provider=THIRD_PARTY, original_provider=LALAMOVE)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    _, allowed = await reassign.allowed_targets(db, _order(), delivery)
    assert allowed == (LALAMOVE,)


@pytest.mark.asyncio
async def test_an_order_with_no_zone_left_falls_back_to_the_default_matrix():
    """
    `polygon_id` is `SET NULL` when a map version is deleted, and orders written
    before the column existed never had one. Neither should lose the feature —
    a fallback that produces an empty list is indistinguishable, to the person
    looking at the screen, from the whole thing being broken.
    """
    delivery = _delivery(provider=LALAMOVE, polygon_id=None)
    order = _order()
    order.shipping_address_snapshot = {}
    db = _Db(delivery, None)
    preferred, allowed = await reassign.allowed_targets(db, order, delivery)
    assert preferred == LALAMOVE
    assert allowed == (THIRD_PARTY,)


@pytest.mark.asyncio
async def test_a_moved_order_with_no_zone_remembers_what_the_map_chose():
    """
    `original_provider` outranks `provider` in the fallback. An order already
    moved once must not have its new home mistaken for the zone's own answer,
    or the second move would be offered a different set than the first.
    """
    delivery = _delivery(
        provider=THIRD_PARTY, original_provider=NOON_SEND, polygon_id=None
    )
    order = _order()
    order.shipping_address_snapshot = {}
    preferred, allowed = await reassign.allowed_targets(_Db(delivery), order, delivery)
    assert preferred == NOON_SEND
    assert set(allowed) == {NOON_SEND, LALAMOVE}


# ── whether it may move at all ────────────────────────────────────────────────


def test_a_collection_order_has_nobody_to_change():
    order = _order(delivery_method=DeliveryMethodEnum.PICKUP)
    assert "collection" in reassign.refuse(order, _delivery())


@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
        OrderStatusEnum.PAYMENT_FAILED,
        OrderStatusEnum.CREATED,
    ],
)
def test_an_order_that_has_left_or_ended_cannot_be_moved(status):
    assert reassign.refuse(_order(status), _delivery()) is not None


@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
        OrderStatusEnum.PACKED,
        OrderStatusEnum.UNDELIVERED,
    ],
)
def test_an_order_still_in_the_kitchen_or_back_from_a_door_can_be_moved(status):
    assert reassign.refuse(_order(status), _delivery()) is None


def test_a_collected_parcel_is_refused_even_while_the_order_status_lags():
    """
    The courier's word beats ours here, and that is the whole point of asking.
    A `PICKED_UP` push that has not yet moved the order still means a rider is
    holding the box, and "the order says packed" is not a reason to send a
    second one.
    """
    delivery = _delivery(provider=LALAMOVE, courier_status="PICKED_UP")
    reason = reassign.refuse(_order(OrderStatusEnum.PACKED), delivery)
    assert reason is not None and "already has this order" in reason


def test_a_driver_on_the_way_blocks_the_move_and_names_the_way_out():
    delivery = _delivery(
        provider=LALAMOVE,
        courier_status="ASSIGNING_DRIVER",
        driver_id="d1",
        driver_name="Imran",
    )
    reason = reassign.refuse(_order(), delivery)
    assert reason is not None
    assert "Imran" in reason and "Abandon the booking" in reason


def test_a_dead_booking_does_not_count_as_a_driver_on_the_way():
    """
    The case the whole feature exists for. Lalamove rejected or expired the
    booking, so whatever name the row still carries, nobody is coming — and
    this is exactly when somebody needs to move the order.
    """
    delivery = _delivery(
        provider=LALAMOVE,
        courier_status="EXPIRED",
        driver_id="d1",
        driver_name="Imran",
    )
    assert reassign.refuse(_order(), delivery) is None


def test_an_order_whose_money_went_back_is_not_ours_to_deliver():
    """
    The guard that carries the history. Orders that reached `undelivered` while
    it was still an ending were refunded automatically on the way in; the money
    is at a bank and no column we write now takes it back, so re-delivering one
    would be handing over a cake the customer has already been paid for.
    """
    order = _order(OrderStatusEnum.UNDELIVERED)
    order.refunded_amount = Decimal("120.00")
    reason = reassign.refuse(order, _delivery())
    assert reason is not None and "120.00" in reason


def test_a_failed_handover_that_was_not_refunded_can_be_tried_elsewhere():
    order = _order(OrderStatusEnum.UNDELIVERED)
    order.refunded_amount = Decimal("0")
    assert reassign.refuse(order, _delivery()) is None


# ── what abandoning would cost ────────────────────────────────────────────────


def test_a_booking_with_no_driver_yet_is_free_to_call_off():
    exposure = reassign.exposure_of(
        _delivery(
            provider=LALAMOVE, courier_order_id="L1", courier_status="ASSIGNING_DRIVER"
        )
    )
    assert exposure.will_be_charged is False
    assert "free" in exposure.reason


def test_a_booking_with_a_driver_may_cost_and_never_says_how_much():
    """
    No figure, ever. Neither courier quotes a cancellation fee over an API, and
    a number worked out from a published tariff would sit on screen next to
    real quoted costs and read as one of them — then be wrong the first time an
    invoice disagreed.
    """
    exposure = reassign.exposure_of(
        _delivery(
            provider=LALAMOVE,
            courier_order_id="L1",
            courier_status="ON_GOING",
            driver_name="Imran",
        )
    )
    assert exposure.will_be_charged is True
    assert not any(ch.isdigit() for ch in exposure.reason)


def test_a_failed_booking_costs_nothing_to_abandon():
    exposure = reassign.exposure_of(
        _delivery(provider=LALAMOVE, courier_order_id="L1", courier_status="EXPIRED")
    )
    assert exposure.will_be_charged is False


# ── what moving actually does ─────────────────────────────────────────────────


@pytest.fixture
def quiet(monkeypatch):
    """
    Stub everything `move` delegates to, and record the order it called them in.

    Order is the assertion in half these tests, so the recorder is a single
    list rather than a call count per service: "cancelled the old booking
    before making a new one" is a fact about the sequence, and a fake that
    cannot see the sequence cannot check it.
    """
    from app.services import (
        batching_service,
        courier_service,
        driver_assignment,
        email_service,
        lalamove_service,
        noon_send_service,
    )

    calls: list[str] = []

    async def cancel(db, order):
        calls.append("cancel_courier")
        return None

    async def cancel_assignment(db, delivery):
        calls.append("leave_batch")

    async def clear(db, delivery, **kw):
        calls.append("clear_driver")

    async def repair(db, order):
        calls.append("email_repair")
        return None

    async def quote_for_order(db, order):
        calls.append("quote")
        return (
            SimpleNamespace(
                estimate=SimpleNamespace(
                    cost=Decimal("22.00"),
                    currency="AED",
                    distance_m=9000,
                    quotation_id="q1",
                ),
                stops=[{"stopId": "a"}, {"stopId": "b"}],
                expires_at=NOW,
            ),
            None,
        )

    async def assign_and_dispatch(db, order, *, quote):
        calls.append("book_lalamove")
        delivery = db.delivery
        delivery.courier_order_id = "L-NEW"
        delivery.courier_status = "ASSIGNING_DRIVER"
        return delivery

    async def dispatch_noon(db, order):
        calls.append("book_noon")
        delivery = db.delivery
        delivery.courier_order_id = "N-NEW"
        delivery.courier_status = "pending_assignment"
        return delivery

    # Credentials are absent in a unit run, and `is_enabled` is a real gate
    # with its own tests — stubbed here so these tests fail for the reason they
    # are about rather than for a missing API key.
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)
    monkeypatch.setattr(courier_service, "cancel", cancel)
    monkeypatch.setattr(batching_service, "cancel_assignment", cancel_assignment)
    monkeypatch.setattr(driver_assignment, "clear", clear)
    monkeypatch.setattr(email_service, "repair_after_reassignment", repair)
    monkeypatch.setattr(lalamove_service, "quote_for_order", quote_for_order)
    monkeypatch.setattr(lalamove_service, "assign_and_dispatch", assign_and_dispatch)
    monkeypatch.setattr(noon_send_service, "dispatch_order", dispatch_noon)
    return calls


@pytest.mark.asyncio
async def test_the_old_booking_is_called_off_before_a_new_one_is_made(quiet):
    """
    The ordering that stops two drivers reaching one cake.

    `order_deliveries` holds exactly one booking per order, so booking first
    would overwrite `courier_order_id` while the old booking is still alive on
    the courier's side — leaving nothing that knows to cancel it.
    """
    delivery = _delivery(
        provider=NOON_SEND,
        courier_order_id="N-OLD",
        courier_status="pending_assignment",
    )
    db = _Db(delivery, _polygon(NOON_SEND, ["third_party", "lalamove"]))
    await reassign.move(db, _order(), target=LALAMOVE, quotation_id="q1")

    assert quiet.index("cancel_courier") < quiet.index("book_lalamove")
    assert quiet.index("leave_batch") < quiet.index("book_lalamove")
    assert quiet.index("clear_driver") < quiet.index("book_lalamove")


@pytest.mark.asyncio
async def test_the_superseded_booking_is_kept_rather_than_forgotten(quiet):
    """A webhook arriving late for the old booking has to be reconcilable."""
    delivery = _delivery(
        provider=NOON_SEND,
        courier_order_id="N-OLD",
        courier_status="pending_assignment",
    )
    db = _Db(delivery, _polygon(NOON_SEND, ["third_party", "lalamove"]))
    await reassign.move(db, _order(), target=LALAMOVE, quotation_id="q1")
    assert "N-OLD" in delivery.previous_courier_order_ids


@pytest.mark.asyncio
async def test_moving_to_a_third_party_books_nothing_and_clears_the_dead_status(quiet):
    """
    The `courier_status` clear is not tidying.

    `fulfilment_service.for_order` pins the customer's stage to `undelivered`
    whenever the delivery row still says so, whatever the order status is. A
    third party never writes that column again — so an order left carrying a
    stale `undelivered` would show "Delivery attempted" on the customer's
    tracking page for the rest of its life, with no estimate and no way out.
    """
    delivery = _delivery(
        provider=LALAMOVE, courier_order_id="L-OLD", courier_status="undelivered"
    )
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    result = await reassign.move(
        db, _order(OrderStatusEnum.UNDELIVERED), target=THIRD_PARTY
    )

    assert result.provider == THIRD_PARTY
    assert result.courier_status is None
    assert result.courier_order_id is None
    assert "book_lalamove" not in quiet and "book_noon" not in quiet


@pytest.mark.asyncio
async def test_the_customers_delivery_fee_never_moves(quiet):
    """They paid their zone's published fee. Only our cost changes."""
    delivery = _delivery(provider=THIRD_PARTY, fee_charged=Decimal("30.00"))
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    result = await reassign.move(db, _order(), target=LALAMOVE, quotation_id="q1")
    assert result.fee_charged == Decimal("30.00")


@pytest.mark.asyncio
async def test_what_the_map_chose_is_recorded_once_and_never_rewritten(quiet):
    """
    `original_provider` means "what the zone chose for this order". An order
    moved twice must keep the first answer — `fulfilment_service` reads it to
    decide whether this customer was ever promised an hour, and a later hop
    overwriting it turns "tomorrow before 10 PM" into a time nobody offered.
    """
    delivery = _delivery(provider=THIRD_PARTY)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))

    await reassign.move(db, _order(), target=LALAMOVE, quotation_id="q1")
    assert delivery.original_provider == THIRD_PARTY

    delivery.courier_order_id = None
    delivery.courier_status = None
    await reassign.move(db, _order(), target=THIRD_PARTY)
    assert delivery.original_provider == THIRD_PARTY, "the first answer, still"


@pytest.mark.asyncio
async def test_a_price_that_moved_is_refused_with_the_new_one_attached(quiet):
    """
    Five minutes is easy to miss, and the whole point of two steps is that a
    person agreed to a figure. Booking at a different one would spend money
    nobody approved.
    """
    delivery = _delivery(provider=THIRD_PARTY)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    with pytest.raises(ConflictError) as caught:
        await reassign.move(db, _order(), target=LALAMOVE, quotation_id="stale")
    assert caught.value.payload["quote"]["quotation_id"] == "q1"
    assert "book_lalamove" not in quiet


@pytest.mark.asyncio
async def test_a_booking_we_could_not_call_off_stops_the_whole_move(quiet, monkeypatch):
    """
    The one place this module refuses rather than degrades. Carrying on would
    leave a live booking on a courier's system for a parcel that has just gone
    out with somebody else.
    """
    from app.services import courier_service

    async def failing_cancel(db, order):
        quiet.append("cancel_courier")
        db.delivery.last_error = "Could not cancel with the courier: 503"
        return db.delivery

    monkeypatch.setattr(courier_service, "cancel", failing_cancel)
    delivery = _delivery(
        provider=LALAMOVE, courier_order_id="L-OLD", courier_status="ASSIGNING_DRIVER"
    )
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))

    with pytest.raises(ConflictError) as caught:
        await reassign.move(db, _order(), target=THIRD_PARTY)
    assert "Cancel it with lalamove directly" in str(caught.value)
    assert delivery.provider == LALAMOVE, "nothing moved"


@pytest.mark.asyncio
async def test_the_row_is_locked_before_anything_is_decided(quiet):
    """
    Two admins pressing at once. The second waits, re-reads, and finds the
    provider already changed — so it is refused by the ordinary gates rather
    than booking a second courier for a cake that now has one.
    """
    delivery = _delivery(provider=THIRD_PARTY)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    await reassign.move(db, _order(), target=LALAMOVE, quotation_id="q1")
    assert db.locked


@pytest.mark.asyncio
async def test_a_zone_that_forbids_the_target_refuses_it(quiet):
    delivery = _delivery(provider=LALAMOVE)
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    with pytest.raises(ConflictError) as caught:
        await reassign.move(db, _order(), target=NOON_SEND)
    assert "does not allow moving to noon_send" in str(caught.value)


@pytest.mark.asyncio
async def test_abandoning_leaves_the_order_where_it_is_with_no_booking(quiet):
    """
    Not a move. The order keeps its courier and loses its booking, which is a
    state every dispatch path already understands — and which unblocks the
    reassignment that a driver on the way was refusing.
    """
    delivery = _delivery(
        provider=LALAMOVE,
        courier_order_id="L-OLD",
        courier_status="ASSIGNING_DRIVER",
        driver_name="Imran",
    )
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    result = await reassign.abandon_booking(db, _order())

    assert result.provider == LALAMOVE, "abandoning is not moving"
    assert result.courier_order_id is None
    assert "L-OLD" in result.previous_courier_order_ids
    assert result.last_error and "needs a courier" in result.last_error
    assert "cancel_courier" in quiet and "clear_driver" in quiet


@pytest.mark.asyncio
async def test_abandoning_never_winds_the_driver_counter_back(quiet):
    """
    The register decides a driver slip is owed by comparing this against its own
    ledger, so it has to be monotonic. Winding it back would make the next
    driver's slip look like one already printed, and the counter would meet a
    stranger holding a bag with somebody else's name on the paper.
    """
    delivery = _delivery(
        provider=LALAMOVE, courier_order_id="L-OLD", courier_status="ASSIGNING_DRIVER"
    )
    delivery.driver_assignment_count = 2
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    result = await reassign.abandon_booking(db, _order())
    assert result.driver_assignment_count == 2


@pytest.mark.asyncio
async def test_a_collected_parcel_cannot_have_its_booking_abandoned(quiet):
    delivery = _delivery(
        provider=LALAMOVE, courier_order_id="L-OLD", courier_status="PICKED_UP"
    )
    db = _Db(delivery, _polygon(LALAMOVE, ["third_party"]))
    with pytest.raises(ConflictError):
        await reassign.abandon_booking(db, _order())
