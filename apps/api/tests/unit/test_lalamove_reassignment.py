"""
Moving a packed third-party order onto Lalamove, and what must not break.

A zone marked `third_party` has no integration by design — somebody we already
use collects the box and tells us nothing. This is the escape hatch for the day
that is not good enough, and the two things it must never do are book at a
price nobody agreed to, and leave a paid, packed order pointed at a courier
that is not coming.

The flow is deliberately two calls. An admin is agreeing to a specific figure,
and a single call that quoted and booked in one breath would be asking them to
approve a number they had not seen.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services.couriers import lalamove_service
from app.services.providers import lalamove_provider
from app.services.providers.lalamove_provider import LalamoveError

QUOTATION_ID = "1514140994227007571"
COURIER_ORDER_ID = "3463513590991397204"


class _Recorder:
    """Stands in for `httpx.AsyncClient` and keeps every request it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def request(self, method, url, *, content=None, headers=None, **_kwargs):
        self.requests.append({"method": method, "url": url, "content": content})
        return self.responses.pop(0)


def _response(status: int, payload) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://rest.lalamove.com/v3/quotations"),
    )


def _quotation(total: str = "45") -> dict:
    return {
        "data": {
            "quotationId": QUOTATION_ID,
            "expiresAt": "2026-08-08T12:05:00.00Z",
            "priceBreakdown": {"total": total, "currency": "AED"},
            "distance": {"value": "7200", "unit": "m"},
            "stops": [{"stopId": "1000"}, {"stopId": "1001"}],
        }
    }


def _booking() -> dict:
    return {
        "data": {
            "orderId": COURIER_ORDER_ID,
            "status": "ASSIGNING_DRIVER",
            "shareLink": "https://share.lalamove.com/abc",
            "priceBreakdown": {"total": "45", "currency": "AED"},
        }
    }


def _order() -> Order:
    return Order(
        id=uuid.uuid4(),
        order_number="MM-20260808-001",
        status=OrderStatusEnum.PACKED,
        branch_id=uuid.uuid4(),
        shipping_address_snapshot={
            "latitude": 25.2048,
            "longitude": 55.2708,
            "phone": "+971501234567",
            "name": "A Customer",
            "address_line1": "Somewhere",
        },
    )


def _delivery(**over) -> OrderDelivery:
    base = dict(
        order_id=uuid.uuid4(),
        provider="third_party",
        zone_name="Al Ain",
        fee_charged=Decimal("20.00"),
        previous_courier_order_ids=[],
    )
    base.update(over)
    return OrderDelivery(**base)


class _Db:
    """Answers only what the reassignment path asks: the delivery for an order."""

    def __init__(self, delivery: OrderDelivery | None):
        self.delivery = delivery
        self.commits = 0

    async def execute(self, _stmt):
        delivery = self.delivery
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: delivery, all=lambda: [])
        )

    async def commit(self):
        self.commits += 1

    async def flush(self):
        pass


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Credentials present, and the pickup branch resolved without a database."""
    monkeypatch.setattr(lalamove_provider.settings, "LALAMOVE_API_KEY", "pk_test_x")
    monkeypatch.setattr(lalamove_provider.settings, "LALAMOVE_API_SECRET", "sk_test_x")
    monkeypatch.setattr(
        lalamove_service,
        "resolve_pickup",
        _fake_pickup,
    )
    monkeypatch.setattr(lalamove_service.courier_reference, "assign", _fake_reference)


async def _fake_pickup(_db, _branch_id=None):
    return lalamove_service.PickupPoint(
        name="Sharjah Kitchen",
        phone="+971600500500",
        address="Sharjah",
        latitude=25.3463,
        longitude=55.4209,
    )


async def _fake_reference(_db, delivery):
    """What the real `courier_reference.assign` does: stamp it and return it.

    Returning the string without writing the column would have this fixture
    disagree with production about whether the driver has a number to read
    back — the kind of gap that makes a passing test meaningless.
    """
    delivery.courier_reference = "1234567"
    return delivery.courier_reference


# ── quoting ───────────────────────────────────────────────────────────────────


async def test_a_quote_prices_the_order_s_own_address(monkeypatch):
    """
    Not `estimate_for_point`, which caches for two minutes on a coordinate
    rounded to about eleven metres — right for a customer dragging a pin around
    a checkout, wrong for a figure somebody is about to spend money against.
    """
    recorder = _Recorder([_response(201, _quotation())])
    monkeypatch.setattr(lalamove_provider.httpx, "AsyncClient", recorder)

    quote, error = await lalamove_service.quote_for_order(_Db(None), _order())

    assert error is None
    assert quote is not None
    assert quote.estimate.cost == Decimal("45")
    assert quote.estimate.currency == "AED"
    assert quote.estimate.distance_m == 7200
    assert quote.estimate.quotation_id == QUOTATION_ID
    # The drop is the order's real pin, not a rounded one.
    sent = json.loads(recorder.requests[0]["content"])["data"]
    assert sent["stops"][1]["coordinates"] == {
        "lat": "25.2048000",
        "lng": "55.2708000",
    }


async def test_an_unreachable_address_is_a_sentence_not_an_exception(monkeypatch):
    recorder = _Recorder([_response(422, {"message": "ERR_OUT_OF_SERVICE_AREA"})] * 2)
    monkeypatch.setattr(lalamove_provider.httpx, "AsyncClient", recorder)

    quote, error = await lalamove_service.quote_for_order(_Db(None), _order())
    assert quote is None
    assert error == "Address is outside the courier's service area"


async def test_an_order_with_no_phone_cannot_be_quoted(monkeypatch):
    order = _order()
    order.shipping_address_snapshot = {"latitude": 25.2, "longitude": 55.2}
    quote, error = await lalamove_service.quote_for_order(_Db(None), order)
    assert quote is None
    assert error == "Order has no reachable phone number"


# ── assigning ─────────────────────────────────────────────────────────────────


async def test_assigning_books_at_the_approved_price_and_changes_hands(monkeypatch):
    recorder = _Recorder([_response(201, _quotation()), _response(201, _booking())])
    monkeypatch.setattr(lalamove_provider.httpx, "AsyncClient", recorder)

    order, delivery = _order(), _delivery()
    db = _Db(delivery)

    quote, _ = await lalamove_service.quote_for_order(db, order)
    result = await lalamove_service.assign_and_dispatch(db, order, quote=quote)

    assert result.provider == "lalamove"
    assert result.original_provider == "third_party", "the record that it moved"
    assert result.courier_order_id == COURIER_ORDER_ID
    assert result.share_link == "https://share.lalamove.com/abc"
    assert result.courier_reference == "1234567"
    assert result.last_error is None

    # The booking quotes the id that was approved, not a fresh one.
    booked = json.loads(recorder.requests[1]["content"])["data"]
    assert booked["quotationId"] == QUOTATION_ID

    # What the customer paid is untouched; the quote is our cost.
    assert result.fee_charged == Decimal("20.00")
    assert result.quoted_cost == Decimal("45")

    # A third-party order never reaches `assign_or_dispatch`, so this has been
    # null for its whole life — and with it the timeline's "packed" stamp.
    assert result.dispatchable_at is not None

    # Committed on the spot: a debited wallet does not roll back with us.
    assert db.commits == 1


async def test_a_failed_booking_puts_the_provider_back(monkeypatch):
    """
    The one state that strands a paid, packed order: a row saying `lalamove`
    with no booking. Every dispatch path would believe a courier is coming, and
    the third-party flow that was actually going to deliver it no longer
    applies.
    """
    recorder = _Recorder(
        [
            _response(201, _quotation()),
            _response(402, {"message": "ERR_INSUFFICIENT_CREDIT"}),
            _response(402, {"message": "ERR_INSUFFICIENT_CREDIT"}),
        ]
    )
    monkeypatch.setattr(lalamove_provider.httpx, "AsyncClient", recorder)

    order, delivery = _order(), _delivery()
    db = _Db(delivery)
    quote, _ = await lalamove_service.quote_for_order(db, order)
    result = await lalamove_service.assign_and_dispatch(db, order, quote=quote)

    assert result.provider == "third_party"
    assert result.original_provider is None
    assert result.courier_order_id is None
    assert "ERR_INSUFFICIENT_CREDIT" in (result.last_error or "")
    assert db.commits == 0


async def test_an_expired_quotation_refuses_rather_than_re_pricing(monkeypatch):
    """
    Five minutes is easy to miss. Booking anyway at whatever the price has
    become would spend money nobody approved, so this raises and the caller
    quotes again and asks again.
    """
    recorder = _Recorder(
        [
            _response(201, _quotation()),
            _response(422, {"message": "ERR_INVALID_QUOTATION_ID"}),
            _response(422, {"message": "ERR_INVALID_QUOTATION_ID"}),
        ]
    )
    monkeypatch.setattr(lalamove_provider.httpx, "AsyncClient", recorder)

    order, delivery = _order(), _delivery()
    db = _Db(delivery)
    quote, _ = await lalamove_service.quote_for_order(db, order)

    with pytest.raises(lalamove_service.QuotationExpired):
        await lalamove_service.assign_and_dispatch(db, order, quote=quote)

    # Nothing was written, so asking again is free.
    assert delivery.provider == "third_party"
    assert delivery.courier_order_id is None
    assert db.commits == 0


def test_an_expired_quotation_is_told_apart_from_a_bad_one():
    """
    Its own predicate, because it is the one failure that is not a fault:
    somebody was shown a price and took too long to agree to it.
    """
    expired = LalamoveError("nope", status=422, error_id="ERR_INVALID_QUOTATION_ID")
    assert expired.is_expired_quotation
    assert not expired.is_out_of_service_area
    assert not LalamoveError(
        "nope", status=422, error_id="ERR_OUT_OF_SERVICE_AREA"
    ).is_expired_quotation


# ── ORDER_REPLACED ────────────────────────────────────────────────────────────


def _replacement(new_id: str, previous_id: str) -> dict:
    """The event Lalamove sends when it cancels and clones an order.

    Taken from their webhook v1.5 documentation: the id in `data.order` is the
    *new* booking, and `prevOrderId` is the one we are holding.
    """
    return {
        "eventType": "ORDER_REPLACED",
        "eventId": "8B3C0D2E-0000-0000-0000-000000000001",
        "timestamp": 1785778684,
        "data": {
            "order": {"orderId": new_id},
            "prevOrderId": previous_id,
            "updatedAt": "2026-08-03T17:38.00Z",
        },
    }


def test_a_replacement_is_looked_up_by_the_booking_it_supersedes():
    payload = _replacement("999", COURIER_ORDER_ID)
    assert lalamove_service.courier_order_id_of(payload) == "999"
    assert lalamove_service.replaced_order_id_of(payload) == COURIER_ORDER_ID
    # The id that finds our row is the old one, for this event type only.
    assert lalamove_service.delivery_key_of(payload) == COURIER_ORDER_ID
    assert (
        lalamove_service.delivery_key_of(
            {"eventType": "ORDER_STATUS_CHANGED", "data": {"order": {"orderId": "7"}}}
        )
        == "7"
    )


async def test_a_replacement_repoints_the_row_and_clears_the_phantom_cancellation():
    """
    Lalamove cancel-and-clones an order to adjust it after matching, and the
    `CANCELED` that arrives first is indistinguishable from a real one. Left
    alone, `needs_attention` goes true, the order lands on the re-dispatch list,
    and re-dispatching puts a **second driver on the same cake** while the first
    is still carrying it.
    """
    delivery = _delivery(
        provider="lalamove",
        courier_order_id=COURIER_ORDER_ID,
        courier_status="CANCELED",
        cancelled_at=datetime(2026, 8, 3, 17, 30, tzinfo=timezone.utc),
        cancel_party="LALAMOVE_CUSTOMER_SUPPORT",
        cancel_reason="other",
        last_error="Courier canceled the booking — re-dispatch required",
    )

    await lalamove_service.apply_webhook(
        _Db(delivery), _replacement("3463513590991397328", COURIER_ORDER_ID), delivery
    )

    assert delivery.courier_order_id == "3463513590991397328"
    assert delivery.previous_courier_order_ids == [COURIER_ORDER_ID]
    assert delivery.cancelled_at is None
    assert delivery.cancel_reason is None
    assert delivery.last_error is None
    assert delivery.needs_attention is False
    assert delivery.courier_status == "ASSIGNING_DRIVER"


async def test_a_replacement_naming_the_id_we_already_hold_changes_nothing():
    delivery = _delivery(provider="lalamove", courier_order_id=COURIER_ORDER_ID)
    await lalamove_service.apply_webhook(
        _Db(delivery), _replacement(COURIER_ORDER_ID, COURIER_ORDER_ID), delivery
    )
    assert delivery.previous_courier_order_ids == []
    assert delivery.courier_order_id == COURIER_ORDER_ID
