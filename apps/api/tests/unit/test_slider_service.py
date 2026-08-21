"""
The Slider bridge: the vehicle rule, the ceilings, and the inbound pushes.

Three things about this courier are not like the other two, and each one is a
place a bug would be silent rather than loud.

**The vehicle is priced, and there is no quotation id.** Slider quotes a bike
and a car at different prices and gives us nothing to quote back at booking
time, so the estimate and the booking must arrive at the same tier by
themselves. `vehicle_for` is the single function both call, and the last test in
that section is the one that says so.

**There is no coverage endpoint and `/deliveries/fare` is not one** — it priced
Riyadh, Muscat and Liwa at 345 km. The only refusal is a 422 at creation, which
is why `SliderError.is_unserviceable` is load-bearing.

**Their webhook has no event id and no ordering guarantee.** The dedup key is
synthesised and the ordering comes from the statuses themselves.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.models.order_delivery import (
    SLIDER_FAILED_STATUSES,
    SLIDER_STATUS_RANK,
    SLIDER_TERMINAL_STATUSES,
    SliderStatusEnum,
    is_collected,
    is_failed,
    is_terminal,
)
from app.services import slider_service
from app.services.providers import slider_provider
from app.services.providers.slider_provider import (
    SliderClient,
    SliderConfig,
    SliderError,
)


# ── the vehicle rule ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "road_km,expected",
    [(0.5, "bike"), (34.9, "bike"), (35.0, "bike"), (35.1, "car"), (120.0, "car")],
)
def test_the_bike_stops_at_the_configured_road_distance(road_km, expected):
    """
    Inclusive at the boundary: 35.0 is "no more than 35", which is what the
    limit says. Pinned at three points either side because an off-by-one here
    is a fare difference nobody would look for.
    """
    assert slider_service.vehicle_for(in_same_emirate=True, road_km=road_km) == expected


@pytest.mark.parametrize("road_km", [0.5, 10.0, 34.9])
def test_a_bike_never_crosses_an_emirate_boundary(road_km):
    """
    Both conditions, not just distance. From the Sharjah kitchen this makes the
    bike tier usable inside Sharjah and nowhere else, however short the run —
    10 km into Ajman is a car.

    Slider's own API disagrees: `/deliveries/fare` offered a bike for
    Sharjah→Ajman at 12 km and for nine Sharjah→Dubai routes out to 34.4 km.
    Until somebody at Slider answers that in writing this takes the stricter
    reading, and this test is where the answer lands if it changes.
    """
    assert slider_service.vehicle_for(in_same_emirate=False, road_km=road_km) == "car"


def test_an_unknown_distance_takes_the_car():
    """Not knowing how far it is is not a reason to send the limited vehicle."""
    assert slider_service.vehicle_for(in_same_emirate=True, road_km=None) == "car"


def test_the_limit_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "SLIDER_BIKE_MAX_KM", 12.0)
    assert slider_service.vehicle_for(in_same_emirate=True, road_km=12.0) == "bike"
    assert slider_service.vehicle_for(in_same_emirate=True, road_km=12.1) == "car"


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("Sharjah", "Sharjah", True),
        ("Sharjah", "sharjah", True),
        ("Umm al-Quwain", "Umm Al Quwain", True),
        ("Sharjah", "Dubai", False),
        # "We do not know" must fall to the car, which is allowed to cross.
        ("", "Sharjah", False),
        ("Sharjah", None, False),
    ],
)
def test_emirates_are_compared_as_places_rather_than_as_strings(left, right, expected):
    assert slider_service.same_emirate(left, right) is expected


def test_thirty_five_road_km_is_not_thirty_five_straight_line(monkeypatch):
    """
    The trap the rule is written around. The survey measured the detour factor
    at 1.44, so 35 road km is about 24 km crow-flies — comparing the threshold
    against a straight line would put half of Dubai on a bike.
    """
    monkeypatch.setattr(settings, "SLIDER_DETOUR_FACTOR", 1.44)
    # A drop 30 km away in a straight line is 43 road km, which is a car.
    road = slider_service.road_distance_km(25.3304139, 55.3736131, 25.0655, 55.3736131)
    assert road > 35.0
    assert slider_service.vehicle_for(in_same_emirate=True, road_km=road) == "car"


# ── the fare response ─────────────────────────────────────────────────────────

FARE = {
    "distance_km": 12.4,
    "bike": {"is_available": True, "fare": "18.50", "distance_km": 12.4},
    "car": {"is_available": True, "fare": "26.00", "distance_km": 12.4},
}


def test_a_fare_is_read_for_the_tier_that_was_asked_for():
    assert slider_service._fare(FARE, "bike") == (Decimal("18.50"), 12.4)
    assert slider_service._fare(FARE, "car") == (Decimal("26.00"), 12.4)


def test_an_unavailable_tier_prices_as_nothing():
    payload = {"bike": {"is_available": False, "fare": "18.50"}}
    assert slider_service._fare(payload, "bike") == (None, None)


def test_the_tiers_are_read_whether_or_not_they_are_nested():
    """Both shapes have turned up. Neither changes the two numbers we need."""
    nested = {"vehicles": {"car": {"is_available": True, "total": "26.00"}}}
    assert slider_service._fare(nested, "car")[0] == Decimal("26.00")


def test_a_missing_tier_is_not_a_crash():
    assert slider_service._fare({}, "bike") == (None, None)
    assert slider_service._fare({"bike": "nonsense"}, "bike") == (None, None)


# ── the money ceilings ────────────────────────────────────────────────────────


def _order(**overrides):
    values = {
        "id": uuid.uuid4(),
        "order_number": "MM-1001",
        "branch_id": None,
        "email": "customer@example.com",
        "user_id": uuid.uuid4(),
        "total": Decimal("185.00"),
        "payment_method": "stripe",
        "notes": None,
        "shipping_address_snapshot": {
            "latitude": 25.4052,
            "longitude": 55.4384,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "last_name": "Abbasi",
            "address_line_1": "Ajman Corniche Tower",
            "city": "Ajman",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


PICKUP = SimpleNamespace(
    name="Melting Moments Cakes",
    phone="+971501234567",
    address="Al Majaz 3, Sharjah",
    latitude=25.3304139,
    longitude=55.3736131,
    reference="K001",
    emirate="Sharjah",
)


@pytest.fixture
def pickup(monkeypatch):
    async def resolve(db, branch_id=None):
        return PICKUP

    monkeypatch.setattr(slider_service, "resolve_pickup", resolve)
    return PICKUP


@pytest.mark.asyncio
async def test_a_card_order_up_to_five_hundred_may_be_offered(pickup):
    allowed, reason = await slider_service.may_serve(
        None, _order(total=Decimal("500.00"))
    )
    assert allowed and reason is None


@pytest.mark.asyncio
async def test_a_card_order_over_five_hundred_is_refused_before_a_rider_is_engaged(
    pickup,
):
    """A rejection once a rider has been engaged is a cancellation we pay for."""
    allowed, reason = await slider_service.may_serve(
        None, _order(total=Decimal("500.01"))
    )
    assert not allowed
    assert "500.00" in reason


@pytest.mark.asyncio
async def test_cash_on_delivery_stops_at_three_hundred_and_fifty(pickup):
    over = _order(payment_method="cod", total=Decimal("350.01"))
    under = _order(payment_method="cod", total=Decimal("350.00"))
    assert not (await slider_service.may_serve(None, over))[0]
    assert (await slider_service.may_serve(None, under))[0]
    # And the same value on a card is fine, which is the whole reason there are
    # two numbers rather than one.
    assert (await slider_service.may_serve(None, _order(total=Decimal("350.01"))))[0]


@pytest.mark.asyncio
async def test_an_order_with_no_pin_is_refused(pickup):
    allowed, reason = await slider_service.may_serve(
        None, _order(shipping_address_snapshot={})
    )
    assert not allowed
    assert "coordinates" in reason


# ── the statuses ──────────────────────────────────────────────────────────────


def test_the_status_rank_is_linear_and_covers_every_status():
    """
    The only ordering there is. Their webhook has no event id and no promise
    that pushes arrive in order, so a status missing from this map ranks -1 and
    can never displace one we already hold — which is safe, but silently means
    the order stops moving.
    """
    assert set(SLIDER_STATUS_RANK) == {s.value for s in SliderStatusEnum}
    journey = [
        SliderStatusEnum.SEARCHING_RIDER,
        SliderStatusEnum.RIDER_ASSIGNED,
        SliderStatusEnum.HEADING_TO_PICKUP,
        SliderStatusEnum.AT_PICKUP,
        SliderStatusEnum.PICKED_UP,
        SliderStatusEnum.IN_TRANSIT,
    ]
    ranks = [SLIDER_STATUS_RANK[s.value] for s in journey]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


def test_the_three_endings_rank_together_at_the_top():
    """None of them may be undone by a late push describing something earlier."""
    endings = {
        SLIDER_STATUS_RANK[s]
        for s in (
            SliderStatusEnum.DELIVERED.value,
            SliderStatusEnum.RETURN_TRIP_STARTED.value,
            SliderStatusEnum.CANCELLED.value,
        )
    }
    assert len(endings) == 1
    assert max(SLIDER_STATUS_RANK.values()) in endings


def test_a_returned_parcel_is_a_failure_and_a_delivered_one_is_not():
    assert is_terminal("slider", SliderStatusEnum.DELIVERED.value)
    assert not is_failed("slider", SliderStatusEnum.DELIVERED.value)
    assert is_failed("slider", SliderStatusEnum.RETURN_TRIP_STARTED.value)
    assert is_failed("slider", SliderStatusEnum.CANCELLED.value)
    assert SLIDER_FAILED_STATUSES < SLIDER_TERMINAL_STATUSES


def test_a_rider_on_a_return_trip_is_coming_back_here():
    """
    `is_collected` answers one question — how long until somebody collects this
    — and it stops meaning anything once they have. A return trip is the one
    case where the parcel is on the bike and the bike is heading *towards* the
    kitchen, so it is deliberately not in the set.
    """
    assert is_collected("slider", SliderStatusEnum.PICKED_UP.value)
    assert is_collected("slider", SliderStatusEnum.IN_TRANSIT.value)
    assert not is_collected("slider", SliderStatusEnum.RETURN_TRIP_STARTED.value)
    assert not is_collected("slider", SliderStatusEnum.AT_PICKUP.value)


def test_the_synthesised_event_id_changes_with_the_event_and_not_otherwise():
    """
    Slider sends no event identifier, so the dedup key is the delivery, the
    status and the moment. A genuine retry reproduces all three; a real
    transition changes at least one.
    """
    push = {
        "order_number": "MM-1001",
        "status": "picked_up",
        "timestamp": "2026-08-21T10:00:00Z",
    }
    assert slider_service._event_id(push) == slider_service._event_id(dict(push))
    assert slider_service._event_id(push) != slider_service._event_id(
        {**push, "status": "in_transit"}
    )
    assert slider_service._event_id(push) != slider_service._event_id(
        {**push, "timestamp": "2026-08-21T10:00:01Z"}
    )


# ── the transport ─────────────────────────────────────────────────────────────


def _client(handler, **config) -> SliderClient:
    values = {
        "api_key": "test-key",
        "account_id": "acct_1",
        "env": "https://slider.test",
        "timeout": 2.0,
    }
    values.update(config)
    client = SliderClient(SliderConfig(**values))
    return client


@pytest.mark.asyncio
async def test_every_request_names_itself(monkeypatch):
    """
    Their WAF answers a request with no `User-Agent` with a 403 that looks
    exactly like a bad key. It is why the proof-of-concept worked under `curl`
    and failed under plain Node.
    """
    seen: dict = {}

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def request(self, method, url, json=None, headers=None):
            seen.update(headers or {})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(slider_provider.httpx, "AsyncClient", _Client)
    await _client(None)._call("GET", "/deliveries/1")

    assert seen["User-Agent"] == slider_provider.USER_AGENT
    assert seen["Authorization"] == "Bearer test-key"
    assert seen["X-Account-Id"] == "acct_1"


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_is_an_error_even_under_a_200(monkeypatch):
    """
    Their WAF serves an HTML challenge page with a success status. The
    alternative reading — shrug and return None — puts `undefined` where a fare
    should be and books a delivery at a price nobody quoted.
    """

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def request(self, *_a, **_kw):
            return httpx.Response(
                200,
                text="<html>checking your browser</html>",
                headers={"content-type": "text/html"},
            )

    monkeypatch.setattr(slider_provider.httpx, "AsyncClient", _Client)
    with pytest.raises(SliderError) as exc:
        await _client(None)._call("POST", "/deliveries/fare")
    assert "not JSON" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_transient_status_is_tried_again(monkeypatch, status):
    attempts: list[int] = []

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def request(self, *_a, **_kw):
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(status, json={"message": "later"})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(slider_provider.httpx, "AsyncClient", _Client)
    assert await _client(None)._call("GET", "/deliveries/1") == {"ok": True}
    assert len(attempts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 409, 422])
async def test_a_refusal_is_not_tried_again_and_says_it_is_a_refusal(
    monkeypatch, status
):
    """
    A 4xx from Slider is an answer, and asking again does not change it. The
    422 is the important one: it is the *only* way we ever learn that an address
    is outside their area, so it has to reach the caller as "book somebody else"
    rather than as an outage.
    """
    attempts: list[int] = []

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def request(self, *_a, **_kw):
            attempts.append(1)
            return httpx.Response(status, json={"message": "outside the service area"})

    monkeypatch.setattr(slider_provider.httpx, "AsyncClient", _Client)
    with pytest.raises(SliderError) as exc:
        await _client(None)._call("POST", "/deliveries")

    assert len(attempts) == 1
    assert exc.value.is_unserviceable
    assert "outside the service area" in str(exc.value)


def test_an_absolute_origin_in_the_environment_wins():
    """
    Their hostnames are the one part of this integration that could not be
    verified from inside the repository, and a wrong one fails as DNS on the
    first real booking. Fixing that should not need a deploy.
    """
    assert SliderConfig("k", "a", "https://api.slider.example/", 8.0).host == (
        "https://api.slider.example"
    )
    assert (
        SliderConfig("k", "a", "production", 8.0).host
        == (slider_provider.HOSTS["production"])
    )
    # An unrecognised value takes staging rather than production. Guessing
    # wrong in that direction costs a test order; the other way costs a rider.
    assert (
        SliderConfig("k", "a", "nonsense", 8.0).host
        == (slider_provider.HOSTS["staging"])
    )


def test_an_empty_key_means_not_configured_rather_than_broken():
    assert not SliderConfig("", "a", "production", 8.0).is_configured
    assert SliderConfig("k", "", "production", 8.0).is_configured


# ── the estimate and the booking must agree ───────────────────────────────────


class _Db:
    """Just enough session to hand back one delivery row and take a commit."""

    def __init__(self, delivery):
        self.delivery = delivery
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture
def booked(monkeypatch, pickup):
    """Slider configured, and every call it makes recorded rather than made."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "test-key")
    slider_service.clear_caches()
    sent: dict = {}

    async def fare(*, pickup, drop_off):
        return FARE

    async def create_delivery(**kwargs):
        sent.update(kwargs)
        return {
            "id": "SLD-4820193",
            "status": "searching_rider",
            "tracking_url": "https://track.slider.test/SLD-4820193",
        }

    async def get_delivery(db, order_id):
        return db.delivery

    async def assign(db, delivery):
        return "4820193"

    async def clear(db, delivery, at=None):
        return None

    async def outstanding(db, order):
        return Decimal("0.00")

    monkeypatch.setattr(slider_service.provider, "fare", fare)
    monkeypatch.setattr(slider_service.provider, "create_delivery", create_delivery)
    monkeypatch.setattr(slider_service, "get_delivery", get_delivery)
    monkeypatch.setattr(slider_service.courier_reference, "assign", assign)
    monkeypatch.setattr(slider_service.driver_assignment, "clear", clear)
    monkeypatch.setattr(slider_service, "_outstanding", outstanding)
    return sent


def _row():
    from app.models.order_delivery import OrderDelivery

    return OrderDelivery(order_id=uuid.uuid4(), provider="slider")


@pytest.mark.asyncio
async def test_the_estimate_and_the_booking_choose_the_same_vehicle(booked):
    """
    The failure this whole design exists to prevent. Slider issues no quotation
    id, so nothing carries the tier from the estimate into the booking — if the
    two decided separately they would eventually decide differently, and the
    first anyone would know is an invoice that did not match a quote.

    Ajman from a Sharjah kitchen: a different emirate, so a car, whatever the
    12.4 km says.
    """
    order = _order()
    db = _Db(_row())

    estimate, error = await slider_service.estimate_for_point(
        db,
        25.4052,
        55.4384,
        drop_emirate="Ajman",
    )
    assert error is None
    assert estimate.cost == Decimal("26.00")  # the car fare

    await slider_service.dispatch_order(db, order)
    assert booked["vehicle"] == "car"
    assert db.delivery.quoted_cost == estimate.cost
    assert db.delivery.cost_total == Decimal("26.00")


@pytest.mark.asyncio
async def test_a_drop_inside_sharjah_is_quoted_and_booked_on_the_bike(booked):
    order = _order(
        shipping_address_snapshot={
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "last_name": "Abbasi",
            "address_line_1": "Garden Tower 1, Al Majaz 3",
            "city": "Sharjah",
        }
    )
    db = _Db(_row())

    estimate, _ = await slider_service.estimate_for_point(
        db, 25.3213, 55.3820, drop_emirate="Sharjah"
    )
    assert estimate.cost == Decimal("18.50")  # the bike fare

    await slider_service.dispatch_order(db, order)
    assert booked["vehicle"] == "bike"
    assert db.delivery.cost_total == Decimal("18.50")


@pytest.mark.asyncio
async def test_a_booking_records_the_id_the_tracking_link_and_nothing_else(booked):
    db = _Db(_row())
    delivery = await slider_service.dispatch_order(db, _order())

    assert delivery.courier_order_id == "SLD-4820193"
    assert delivery.courier_status == "searching_rider"
    assert delivery.share_link == "https://track.slider.test/SLD-4820193"
    assert delivery.last_error is None
    # `courier_service._record_outcome` owns the retry ladder, once, on the way
    # out. A provider that touched it would let a rung be skipped depending on
    # which courier happened to answer.
    assert delivery.dispatch_attempts is None
    assert delivery.next_attempt_at is None
    # Committed on the success path only: a rider has been engaged outside our
    # transaction and cannot be rolled back with it.
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_refusal_leaves_the_error_and_no_id_and_no_commit(booked, monkeypatch):
    async def refused(**_kwargs):
        raise SliderError("Slider: outside the service area", status=422)

    monkeypatch.setattr(slider_service.provider, "create_delivery", refused)
    db = _Db(_row())
    delivery = await slider_service.dispatch_order(db, _order())

    assert delivery.courier_order_id is None
    assert "outside the service area" in delivery.last_error
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_failed_fare_call_still_books_but_records_no_cost(booked, monkeypatch):
    """
    The booking is what matters; the price is what we would like. A fare call
    that failed leaves the cost columns empty rather than filling them with
    something computed — unlike noon Send, who publish a rate card and no API,
    Slider quotes, so a figure on this row is always one of theirs.
    """

    async def unreachable(**_kwargs):
        raise SliderError("Slider is unreachable")

    monkeypatch.setattr(slider_service.provider, "fare", unreachable)
    db = _Db(_row())
    delivery = await slider_service.dispatch_order(db, _order())

    assert delivery.courier_order_id == "SLD-4820193"
    assert delivery.cost_total is None
    # And the vehicle still had to be chosen, from the fitted distance.
    assert booked["vehicle"] == "car"


@pytest.mark.asyncio
async def test_an_order_with_no_phone_number_says_so_rather_than_calling_slider(
    booked, monkeypatch
):
    """
    An admin reads `last_error`. "No reachable phone number" is a field they can
    fix; a 422 about `drop_off.contact_phone` is not.
    """
    called: list[int] = []

    async def create(**_kwargs):
        called.append(1)
        return {"id": "x"}

    monkeypatch.setattr(slider_service.provider, "create_delivery", create)
    db = _Db(_row())
    delivery = await slider_service.dispatch_order(
        db,
        _order(
            shipping_address_snapshot={
                "latitude": 25.4052,
                "longitude": 55.4384,
                "address_line_1": "Ajman Corniche Tower",
                "city": "Ajman",
            }
        ),
    )

    assert not called
    assert delivery.last_error == "Order has no reachable phone number"


# ── inbound pushes ────────────────────────────────────────────────────────────


@pytest.fixture
def inbound(monkeypatch):
    """Everything `apply_webhook` reaches for, stubbed. Records the transitions."""
    moved: list[str] = []

    async def record(db, delivery, driver, at=None):
        from app.services.driver_assignment import Change

        delivery.driver_name = driver.name
        delivery.driver_phone = driver.phone
        return Change.UNCHANGED if driver.is_empty else Change.ASSIGNED

    async def record_position(db, delivery, **_kw):
        return None

    async def route_now(db, delivery):
        return None

    async def announce(db, delivery):
        return None

    async def advance(db, delivery, at=None):
        moved.append(delivery.courier_status)

    monkeypatch.setattr(slider_service.driver_assignment, "record", record)
    monkeypatch.setattr(
        slider_service.driver_assignment, "record_position", record_position
    )
    monkeypatch.setattr(slider_service.driver_routing, "route_now", route_now)
    monkeypatch.setattr(slider_service, "_announce_rider", announce)
    monkeypatch.setattr(slider_service, "_advance_order", advance)
    return moved


@pytest.mark.asyncio
async def test_a_status_push_moves_the_delivery_forward(inbound):
    delivery = _row()
    delivery.courier_status = "rider_assigned"
    await slider_service.apply_webhook(
        None,
        {"id": "SLD-1", "status": "picked_up", "timestamp": "2026-08-21T10:00:00Z"},
        delivery,
    )
    assert delivery.courier_status == "picked_up"
    assert delivery.courier_previous_status == "rider_assigned"
    assert inbound == ["picked_up"]


@pytest.mark.asyncio
async def test_a_late_push_does_not_walk_a_delivered_order_backwards(inbound):
    """
    By rank, not by clock. Slider promises no ordering, and the only timestamp
    in the payload is theirs — comparing clocks would mean trusting a field to
    decide whether to trust the payload it arrived in.
    """
    delivery = _row()
    delivery.courier_status = "delivered"
    await slider_service.apply_webhook(
        None,
        # And with a *later* timestamp than the delivery, so a clock-based guard
        # would let it through.
        {"id": "SLD-1", "status": "picked_up", "timestamp": "2099-01-01T00:00:00Z"},
        delivery,
    )
    assert delivery.courier_status == "delivered"
    assert inbound == []


@pytest.mark.asyncio
async def test_a_status_nobody_has_heard_of_cannot_displace_one_we_hold(inbound):
    delivery = _row()
    delivery.courier_status = "in_transit"
    await slider_service.apply_webhook(
        None, {"id": "SLD-1", "status": "some_new_word"}, delivery
    )
    assert delivery.courier_status == "in_transit"


@pytest.mark.asyncio
async def test_a_return_trip_flags_the_row_for_a_human(inbound):
    """
    Nobody is bringing it. The order is not cancelled — it is paid for and
    boxed — so this is a flag for the admin rather than an ending.
    """
    delivery = _row()
    delivery.courier_status = "in_transit"
    await slider_service.apply_webhook(
        None,
        {
            "id": "SLD-1",
            "status": "return_trip_started",
            "timestamp": "2026-08-21T11:00:00Z",
        },
        delivery,
    )
    assert delivery.cancel_reason == "return_trip_started"
    assert "re-dispatch" in delivery.last_error
    assert inbound == ["return_trip_started"]


@pytest.mark.asyncio
async def test_the_rider_is_read_off_the_push_itself(inbound):
    """
    Unlike noon Send, whose status push is three fields and needs a second call
    to find out who is holding the parcel, Slider names the rider inline.
    """
    delivery = _row()
    await slider_service.apply_webhook(
        None,
        {
            "id": "SLD-1",
            "status": "rider_assigned",
            "rider": {"id": "r_88", "name": "Imran", "phone": "+971509876543"},
        },
        delivery,
    )
    assert delivery.driver_name == "Imran"
    assert delivery.driver_phone == "+971509876543"


@pytest.mark.asyncio
async def test_a_tracking_link_on_a_push_is_kept(inbound):
    delivery = _row()
    await slider_service.apply_webhook(
        None,
        {"id": "SLD-1", "status": "picked_up", "tracking_url": "https://t.slider/1"},
        delivery,
    )
    assert delivery.share_link == "https://t.slider/1"
