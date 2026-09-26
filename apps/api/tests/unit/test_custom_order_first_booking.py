"""
A custom order's first courier: priced per courier, booked on exactly that one.

Two failures this path exists to prevent, and each has a test:

* **Money on a courier nobody chose.** `courier_service.dispatch` hands a Slider
  refusal to noon Send or Lalamove. `book_first` must raise instead, having
  called nothing else.
* **A price nobody saw.** Lalamove is booked against the quotation the admin
  approved, read back by id — never a fresh one — and a lapsed quotation is a
  coded 409 the console answers by re-quoting.

And the pricing half never raises for a courier that will not answer: the
quotes screen shows two couriers side by side, and one refusing must not blank
the other.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import BadRequestError, ConflictError
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services.couriers import (
    courier_service,
    lalamove_service,
    noon_send_service,
    slider_service,
)
from app.services.delivery import driver_assignment
from app.services.delivery import fulfilment_reassignment as reassign
from app.services.providers.lalamove_provider import LalamoveError

LALAMOVE = "lalamove"
SLIDER_CAR = "slider_car"

PIN = {"latitude": 25.2048, "longitude": 55.2708}


class _Db:
    """Enough session for `book_first`: the order lock and the delivery read.

    `autoflush=False` like the real sessionmaker — `add` holds a row pending and
    only `flush` publishes it — so a row the code forgot to flush is not
    silently readable.
    """

    def __init__(self, order, delivery=None):
        self.order = order
        self.delivery = delivery
        self.pending: list = []
        self.locked_order = False
        self.commits = 0

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if isinstance(row, OrderDelivery):
                self.delivery = row
        self.pending.clear()

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Order:
            self.locked_order = getattr(stmt, "_for_update_arg", None) is not None
            row = self.order
        else:
            row = self.delivery
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: row))

    async def commit(self):
        self.commits += 1


def _custom(status=OrderStatusEnum.PACKED, source="custom", **overrides) -> Order:
    order = Order(
        id=uuid.uuid4(),
        order_number="MM-C-1",
        status=status,
        source=source,
        delivery_method=DeliveryMethodEnum.DELIVERY,
    )
    order.branch_id = uuid.uuid4()
    order.total = Decimal("1250.00")
    order.payment_method = "bank_transfer"
    order.delivery_fee = Decimal("0")
    order.customer_name = "Aisha Khan"
    order.customer_phone = "+971501234567"
    order.shipping_address_snapshot = {
        **PIN,
        "first_name": "Aisha",
        "last_name": "Khan",
        "phone": "+971501234567",
        "address_line_1": "Villa 12, Al Barsha 2",
    }
    for key, value in overrides.items():
        setattr(order, key, value)
    return order


@pytest.fixture
def calls(monkeypatch):
    """Every courier arm recorded rather than made; the Slider one books."""
    made: list[str] = []

    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)

    async def slider_may_serve(db, order):
        made.append("slider_may_serve")
        return True, None

    async def slider_dispatch(db, order):
        made.append("slider_dispatch")
        db.delivery.courier_order_id = "S-1"
        db.delivery.courier_status = "searching_rider"
        db.delivery.last_error = None
        await db.commit()
        return db.delivery

    async def forbidden(*args, **kwargs):  # pragma: no cover — the assertion
        made.append("FALLBACK")
        raise AssertionError("book_first must not reach another courier")

    async def clear(db, delivery, **kw):
        made.append("clear_driver")

    async def transition(db, order, new_status, **kw):
        made.append(f"transition:{new_status.value}")
        order.status = new_status
        return True

    monkeypatch.setattr(slider_service, "may_serve", slider_may_serve)
    monkeypatch.setattr(slider_service, "dispatch_order", slider_dispatch)
    monkeypatch.setattr(lalamove_service, "dispatch_order", forbidden)
    monkeypatch.setattr(noon_send_service, "dispatch_order", forbidden)
    monkeypatch.setattr(courier_service, "dispatch", forbidden)
    monkeypatch.setattr(driver_assignment, "clear", clear)
    monkeypatch.setattr(reassign.order_lifecycle, "transition", transition)
    return made


def _quotation(**overrides) -> dict:
    quotation = {
        "quotationId": "Q-APPROVED",
        "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat(),
        "priceBreakdown": {"total": "41.00", "currency": "AED"},
        "distance": {"value": "18200"},
        "stops": [
            {"stopId": "s-pickup", "coordinates": {"lat": "25.33", "lng": "55.37"}},
            {
                "stopId": "s-drop",
                "coordinates": {
                    "lat": f"{PIN['latitude']:.7f}",
                    "lng": f"{PIN['longitude']:.7f}",
                },
            },
        ],
    }
    quotation.update(overrides)
    return quotation


@pytest.fixture
def lalamove(monkeypatch, calls):
    """Lalamove answering a read-back of the approved quotation."""
    state = {"quotation": _quotation(), "booked_with": None, "error": None}

    async def get_quotation(quotation_id):
        calls.append(f"get_quotation:{quotation_id}")
        if state["error"] is not None:
            raise state["error"]
        return state["quotation"]

    async def quote_for_order(db, order):  # pragma: no cover — the assertion
        calls.append("REQUOTE")
        raise AssertionError("book_first must not issue a fresh quotation")

    async def assign_and_dispatch(db, order, *, quote):
        calls.append("assign_and_dispatch")
        state["booked_with"] = quote
        db.delivery.courier_order_id = "L-1"
        db.delivery.courier_status = "ASSIGNING_DRIVER"
        await db.commit()
        return db.delivery

    monkeypatch.setattr(lalamove_service.provider, "get_quotation", get_quotation)
    monkeypatch.setattr(lalamove_service, "quote_for_order", quote_for_order)
    monkeypatch.setattr(lalamove_service, "assign_and_dispatch", assign_and_dispatch)
    return state


# ── pricing ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_courier_that_will_not_price_is_a_reason_and_not_an_exception(
    monkeypatch,
):
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)

    async def refuses(db, order):
        return False, "Order has no delivery coordinates"

    monkeypatch.setattr(slider_service, "may_serve", refuses)

    price = await reassign.price_target(None, _custom(), SLIDER_CAR)
    assert price.cost is None
    assert price.reason == "Order has no delivery coordinates"


@pytest.mark.asyncio
async def test_a_lalamove_that_will_not_quote_is_a_reason(monkeypatch):
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)

    async def no_quote(db, order):
        return None, "Address is outside the courier's service area"

    monkeypatch.setattr(lalamove_service, "quote_for_order", no_quote)

    price = await reassign.price_target(None, _custom(), LALAMOVE)
    assert price.cost is None and price.quotation_id is None
    assert "service area" in price.reason


@pytest.mark.asyncio
async def test_an_unconfigured_courier_says_so_rather_than_answering_nothing(
    monkeypatch,
):
    """Slider's `estimate_for_point` answers `(None, None)` for a missing key."""
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: False)
    price = await reassign.price_target(None, _custom(), SLIDER_CAR)
    assert price.cost is None
    assert "not configured" in price.reason


@pytest.mark.asyncio
async def test_slider_is_priced_on_the_car(monkeypatch):
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)
    asked: dict = {}

    async def serves(db, order):
        return True, None

    async def estimate(db, latitude, longitude, **kwargs):
        asked.update(kwargs, latitude=latitude, longitude=longitude)
        return (
            SimpleNamespace(
                cost=Decimal("26.00"),
                currency="AED",
                distance_m=21000,
                quotation_id=None,
            ),
            None,
        )

    monkeypatch.setattr(slider_service, "may_serve", serves)
    monkeypatch.setattr(slider_service, "estimate_for_point", estimate)

    order = _custom()
    price = await reassign.price_target(None, order, SLIDER_CAR)
    assert price.cost == Decimal("26.00") and price.reason is None
    assert asked["vehicle"] == "car"
    assert asked["branch_id"] == order.branch_id
    assert (asked["latitude"], asked["longitude"]) == (
        PIN["latitude"],
        PIN["longitude"],
    )


@pytest.mark.asyncio
async def test_lalamove_is_priced_with_the_quotation_it_must_be_booked_against(
    monkeypatch,
):
    monkeypatch.setattr(courier_service, "is_enabled", lambda provider: True)
    expires = datetime(2026, 9, 26, 10, 5, tzinfo=timezone.utc)

    async def quote(db, order):
        return (
            SimpleNamespace(
                estimate=SimpleNamespace(
                    cost=Decimal("41.00"),
                    currency="AED",
                    distance_m=18200,
                    quotation_id="Q-1",
                ),
                stops=[],
                expires_at=expires,
            ),
            None,
        )

    monkeypatch.setattr(lalamove_service, "quote_for_order", quote)
    price = await reassign.price_target(None, _custom(), LALAMOVE)
    assert (price.cost, price.quotation_id, price.expires_at) == (
        Decimal("41.00"),
        "Q-1",
        expires,
    )


# ── the gates ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_a_custom_order_is_booked_this_way(calls):
    order = _custom(source="online")
    with pytest.raises(BadRequestError, match="custom order"):
        await reassign.book_first(_Db(order), order, SLIDER_CAR)
    assert calls == []


@pytest.mark.asyncio
async def test_a_bike_is_not_a_choice(calls):
    order = _custom()
    with pytest.raises(BadRequestError):
        await reassign.book_first(_Db(order), order, "slider_bike")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
    ],
)
async def test_an_order_not_packed_or_back_from_a_door_is_refused(calls, status):
    order = _custom(status=status)
    db = _Db(order)
    with pytest.raises(ConflictError, match=status.value):
        await reassign.book_first(db, order, SLIDER_CAR)
    assert "slider_dispatch" not in calls
    # Read under the lock, so a status another admin just changed is the one
    # that is judged.
    assert db.locked_order


@pytest.mark.asyncio
async def test_an_order_with_no_pin_is_refused_before_any_courier_is_asked(calls):
    order = _custom()
    order.shipping_address_snapshot = {"address_line_1": "Villa 12"}
    with pytest.raises(BadRequestError, match="location pin"):
        await reassign.book_first(_Db(order), order, SLIDER_CAR)
    assert calls == []


@pytest.mark.asyncio
async def test_an_order_with_no_name_or_phone_is_refused(calls):
    order = _custom(customer_name=None, customer_phone="")
    with pytest.raises(BadRequestError) as caught:
        await reassign.book_first(_Db(order), order, SLIDER_CAR)
    assert "name" in caught.value.detail and "phone" in caught.value.detail


@pytest.mark.asyncio
async def test_lalamove_without_an_approved_quotation_is_refused(calls):
    order = _custom()
    with pytest.raises(BadRequestError, match="price you have seen"):
        await reassign.book_first(_Db(order), order, LALAMOVE)


@pytest.mark.asyncio
async def test_a_live_booking_is_not_replaced_from_here(calls):
    order = _custom(status=OrderStatusEnum.UNDELIVERED)
    delivery = OrderDelivery(order_id=order.id, provider=SLIDER_CAR)
    delivery.courier_order_id = "S-OLD"
    delivery.courier_status = "rider_assigned"
    with pytest.raises(ConflictError, match="live"):
        await reassign.book_first(
            _Db(order, delivery), order, LALAMOVE, quotation_id="Q"
        )
    assert "slider_dispatch" not in calls


# ── Slider ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_first_booking_creates_the_row_on_the_car_and_books_it(calls):
    order = _custom()
    db = _Db(order)
    delivery = await reassign.book_first(db, order, SLIDER_CAR)

    assert delivery.provider == SLIDER_CAR
    assert delivery.courier_order_id == "S-1"
    assert delivery.next_attempt_at is None
    # Not moved from anywhere — the first choice is not a reassignment.
    assert delivery.original_provider is None
    # Already packed: no status step, and the booking arm's commit is the only one.
    assert not any(c.startswith("transition") for c in calls)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_slider_refusal_raises_and_books_nobody_else(calls, monkeypatch):
    """The whole reason this is not `courier_service.dispatch`."""

    async def refuses(db, order):
        calls.append("slider_dispatch")
        db.delivery.last_error = "Slider: address is outside our service area"
        return db.delivery

    monkeypatch.setattr(slider_service, "dispatch_order", refuses)
    order = _custom()
    db = _Db(order)

    with pytest.raises(ConflictError, match="outside our service area"):
        await reassign.book_first(db, order, SLIDER_CAR)

    assert "FALLBACK" not in calls
    # No retry ladder for the sweep to book through `dispatch` behind our back.
    assert db.delivery.next_attempt_at is None
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_slider_gate_refusal_is_raised_before_anything_is_written(
    calls, monkeypatch
):
    async def refuses(db, order):
        return False, "No pickup branch is configured"

    monkeypatch.setattr(slider_service, "may_serve", refuses)
    order = _custom()
    db = _Db(order)
    with pytest.raises(ConflictError, match="pickup branch"):
        await reassign.book_first(db, order, SLIDER_CAR)
    assert db.delivery is None
    assert "slider_dispatch" not in calls


@pytest.mark.asyncio
async def test_after_an_undelivered_the_order_is_packed_and_the_old_booking_kept(
    calls,
):
    order = _custom(status=OrderStatusEnum.UNDELIVERED)
    delivery = OrderDelivery(order_id=order.id, provider=LALAMOVE)
    delivery.courier_order_id = "L-OLD"
    delivery.courier_status = "COMPLETED"
    delivery.previous_courier_order_ids = []
    delivery.dispatch_attempts = 2
    db = _Db(order, delivery)

    booked = await reassign.book_first(db, order, SLIDER_CAR)

    assert booked.courier_order_id == "S-1"
    assert booked.previous_courier_order_ids == ["L-OLD"]
    assert booked.provider == SLIDER_CAR
    assert booked.original_provider == LALAMOVE
    assert booked.dispatch_attempts == 0
    assert order.status == OrderStatusEnum.PACKED
    # Packed *before* the booking, so the arm's commit carries both.
    assert calls.index("transition:packed") < calls.index("slider_dispatch")


# ── Lalamove ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lalamove_is_booked_on_the_quotation_that_was_approved(lalamove, calls):
    order = _custom()
    db = _Db(order)
    delivery = await reassign.book_first(db, order, LALAMOVE, quotation_id="Q-APPROVED")

    assert delivery.courier_order_id == "L-1"
    assert "REQUOTE" not in calls
    quote = lalamove["booked_with"]
    assert quote.estimate.quotation_id == "Q-APPROVED"
    assert quote.estimate.cost == Decimal("41.00")
    assert [s["stopId"] for s in quote.stops] == ["s-pickup", "s-drop"]


@pytest.mark.asyncio
async def test_an_expired_quotation_at_booking_asks_for_a_fresh_one(
    lalamove, monkeypatch
):
    async def expired(db, order, *, quote):
        raise lalamove_service.QuotationExpired("ERR_INVALID_QUOTATION_ID")

    monkeypatch.setattr(lalamove_service, "assign_and_dispatch", expired)
    order = _custom()
    with pytest.raises(reassign.QuotationExpiredError) as caught:
        await reassign.book_first(
            _Db(order), order, LALAMOVE, quotation_id="Q-APPROVED"
        )
    assert caught.value.code == "delivery_quote_expired"
    assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_a_quotation_past_its_expiry_is_refused_without_booking(lalamove, calls):
    lalamove["quotation"] = _quotation(
        expiresAt=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    )
    order = _custom()
    with pytest.raises(reassign.QuotationExpiredError):
        await reassign.book_first(
            _Db(order), order, LALAMOVE, quotation_id="Q-APPROVED"
        )
    assert "assign_and_dispatch" not in calls


@pytest.mark.asyncio
async def test_a_quotation_lalamove_no_longer_knows_is_expired(lalamove, calls):
    lalamove["error"] = LalamoveError(
        "invalid quotation", status=422, error_id="ERR_INVALID_QUOTATION_ID"
    )
    order = _custom()
    with pytest.raises(reassign.QuotationExpiredError):
        await reassign.book_first(_Db(order), order, LALAMOVE, quotation_id="Q-OLD")
    assert "assign_and_dispatch" not in calls


@pytest.mark.asyncio
async def test_a_price_for_a_pin_that_has_since_moved_is_refused(lalamove, calls):
    order = _custom()
    order.shipping_address_snapshot = {
        **order.shipping_address_snapshot,
        "latitude": PIN["latitude"] + 0.01,
    }
    with pytest.raises(reassign.QuotationExpiredError, match="pin has moved"):
        await reassign.book_first(
            _Db(order), order, LALAMOVE, quotation_id="Q-APPROVED"
        )
    assert "assign_and_dispatch" not in calls


# ── the ceiling ───────────────────────────────────────────────────────────────


@pytest.fixture
def pickup(monkeypatch):
    async def resolve(db, branch_id=None):
        return SimpleNamespace(reference="S001", emirate="Sharjah")

    monkeypatch.setattr(slider_service, "resolve_pickup", resolve)


@pytest.mark.asyncio
async def test_a_custom_order_over_the_card_ceiling_may_still_go_by_slider(pickup):
    allowed, reason = await slider_service.may_serve(None, _custom())
    assert allowed and reason is None


@pytest.mark.asyncio
async def test_an_online_order_over_the_card_ceiling_is_still_refused(pickup):
    allowed, reason = await slider_service.may_serve(
        None, _custom(source="online", payment_method="stripe")
    )
    assert not allowed
    assert "500.00" in reason


@pytest.mark.asyncio
async def test_a_custom_order_with_no_pin_is_still_refused(pickup):
    order = _custom()
    order.shipping_address_snapshot = {}
    allowed, reason = await slider_service.may_serve(None, order)
    assert not allowed and "coordinates" in reason
