"""
A courier swapping riders mid-booking, and everything that has to notice.

The old behaviour is worth stating because it looked like it worked: both
services filled the driver only when the row had none — `not had_driver` on the
Lalamove side, `status != current` on the noon Send side — so the *first* rider
was recorded perfectly and every one after them was thrown away. The shop kept a
name and a number belonging to somebody who had dropped the job, the admin card
said the same, and the counter rang them when a different person was at the door.

Nothing here talks to a courier. The whole question — is this the same person —
is decidable from two payloads and a row, which is exactly why it is tested
without a network.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.branch import Branch
from app.models.order_delivery import OrderDelivery
from app.models.order_driver import OrderDriver
from app.services import driver_assignment, driver_proximity
from app.services.driver_assignment import Change, Driver

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


class _Db:
    """
    Enough session to answer "who is on this order" and take a new row.

    The ledger lives in a list rather than a database because every rule under
    test is about which row is active and what the counter is told — none of it
    is about SQL. The one thing the database enforces that this cannot, the
    unique constraint on `(order_id, is_active)`, is asserted here as "exactly
    one row is active" after every move.
    """

    def __init__(self, drivers: list[OrderDriver] | None = None) -> None:
        self.drivers: list[OrderDriver] = list(drivers or [])

    def add(self, row) -> None:
        self.drivers.append(row)

    async def execute(self, stmt):
        active = next((d for d in self.drivers if d.is_active), None)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: active))

    @property
    def active(self) -> list[OrderDriver]:
        return [d for d in self.drivers if d.is_active]


def _delivery(**overrides) -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="lalamove",
        courier_order_id="3463513590991397204",
        courier_status="ON_GOING",
        driver_assignment_count=0,
    )
    for key, value in overrides.items():
        setattr(delivery, key, value)
    return delivery


ALI = Driver(driver_id="79973", name="Ali", phone="+971500000001", plate="A-11111")
SAMEER = Driver(driver_id="80112", name="Sameer", phone="+971500000002", plate="B-2222")


# ── the swap ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_first_driver_opens_a_stint():
    db, delivery = _Db(), _delivery()

    assert await driver_assignment.record(db, delivery, ALI, at=NOW) is Change.ASSIGNED

    assert delivery.driver_name == "Ali"
    assert delivery.driver_assignment_count == 1
    assert delivery.driver_assigned_at == NOW
    assert len(db.active) == 1
    assert db.active[0].sequence == 1


@pytest.mark.asyncio
async def test_a_different_driver_is_a_reassignment_not_an_assignment():
    """
    The distinction the counter acts on.

    An assignment is "somebody is coming"; a reassignment is "somebody *else* is
    coming, and the slip in your hand is wrong". Reported as the same word, the
    second one reads as a duplicate notification and gets dismissed.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)

    change = await driver_assignment.record(
        db, delivery, SAMEER, at=NOW + timedelta(minutes=6)
    )

    assert change is Change.REASSIGNED
    assert delivery.driver_name == "Sameer"
    assert delivery.driver_phone == "+971500000002"
    assert delivery.driver_assignment_count == 2


@pytest.mark.asyncio
async def test_only_one_driver_is_ever_active():
    """
    What `UNIQUE (order_id, is_active)` buys, asserted where the rows are made.

    Two live rows would mean the database could not answer "who is carrying
    order 4", which is the whole reason this is a table and not a JSON column.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)
    await driver_assignment.record(db, delivery, SAMEER, at=NOW + timedelta(minutes=6))
    await driver_assignment.record(db, delivery, ALI, at=NOW + timedelta(minutes=9))

    assert len(db.drivers) == 3
    assert len(db.active) == 1
    assert db.active[0].name == "Ali"
    assert db.active[0].sequence == 3
    # The finished ones are NULL rather than FALSE — a FALSE row would collide
    # with every other finished row under the same constraint.
    assert [d.is_active for d in db.drivers[:2]] == [None, None]
    assert all(d.replaced_at is not None for d in db.drivers[:2])


@pytest.mark.asyncio
async def test_the_replaced_driver_keeps_their_own_stint():
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)
    await driver_assignment.record(db, delivery, SAMEER, at=NOW + timedelta(minutes=6))

    first = db.drivers[0]
    assert first.name == "Ali"
    assert first.phone == "+971500000001"
    assert first.assigned_at == NOW
    assert first.replaced_at == NOW + timedelta(minutes=6)


@pytest.mark.asyncio
async def test_the_same_driver_described_better_is_not_a_swap():
    """
    Lalamove's status push carries a bare `driverId`; the detail call that
    follows carries the name. Two payloads, one person — and if the second one
    counted as a swap the counter would get a second slip for the same rider
    every single time.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, Driver(driver_id="79973"), at=NOW)

    change = await driver_assignment.record(db, delivery, ALI, at=NOW)

    assert change is Change.UNCHANGED
    assert delivery.driver_name == "Ali"
    assert delivery.driver_assignment_count == 1
    assert len(db.drivers) == 1


@pytest.mark.asyncio
async def test_a_payload_naming_nobody_leaves_the_driver_alone():
    """
    Most pushes carry no rider block at all. Each one used to be a chance to
    blank a name the shop was relying on.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)

    change = await driver_assignment.record(db, delivery, Driver(), at=NOW)

    assert change is Change.UNCHANGED
    assert delivery.driver_name == "Ali"


@pytest.mark.asyncio
async def test_noon_send_riders_are_told_apart_by_their_number():
    """
    Their `da_details` has no id in it — a name, a phone and a position is the
    whole block — so identity has to fall back to the number. Two riders sharing
    a first name is ordinary; two sharing a phone is not.
    """
    db = _Db()
    delivery = _delivery(provider="noon_send", courier_status="assigned")
    first = {"name": "Mohammed", "phone_number": "+971500000003"}
    second = {"name": "Mohammed", "phone_number": "+971500000004"}

    await driver_assignment.record(db, delivery, Driver.from_noon_send(first), at=NOW)
    change = await driver_assignment.record(
        db, delivery, Driver.from_noon_send(second), at=NOW + timedelta(minutes=4)
    )

    assert change is Change.REASSIGNED
    assert delivery.driver_phone == "+971500000004"


@pytest.mark.asyncio
async def test_a_swap_drops_the_old_riders_position():
    """
    A pin belongs to a person. Carried across, it would put a stranger's
    distance on the counter's screen under the new driver's name — and it would
    look entirely plausible.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)
    await driver_assignment.record_position(
        db, delivery, latitude=Decimal("25.33"), longitude=Decimal("55.37"), at=NOW
    )

    await driver_assignment.record(db, delivery, SAMEER, at=NOW + timedelta(minutes=6))

    assert delivery.driver_latitude is None
    assert delivery.driver_location_at is None
    # The rider who left keeps theirs; it is where *they* were.
    assert db.drivers[0].latitude == Decimal("25.33")


@pytest.mark.asyncio
async def test_a_redispatch_closes_the_stint_but_not_the_counter():
    """
    `driver_assignment_count` is what the register compares against its own
    ledger to decide a slip is owed, so it must never go backwards.

    Wound back to zero by a re-dispatch, the next driver's slip would carry the
    number of one already printed, the terminal would skip it, and the counter
    would meet a stranger holding a bag with somebody else's name on the paper.
    """
    db, delivery = _Db(), _delivery()
    await driver_assignment.record(db, delivery, ALI, at=NOW)

    await driver_assignment.clear(db, delivery, at=NOW + timedelta(minutes=10))

    assert delivery.driver_name is None
    assert delivery.driver_assignment_count == 1
    assert db.active == []

    await driver_assignment.record(db, delivery, SAMEER, at=NOW + timedelta(minutes=12))
    assert delivery.driver_assignment_count == 2


@pytest.mark.asyncio
async def test_a_driver_predating_the_ledger_is_adopted_rather_than_reassigned():
    """
    Migration 112 backfills a stint for every booking already carrying a driver.
    A row that slipped past it — or one an older code path wrote directly — must
    not have its next confirming payload recorded as a swap.
    """
    db = _Db()
    delivery = _delivery(
        driver_id="79973", driver_name="Ali", driver_assignment_count=0
    )

    change = await driver_assignment.record(db, delivery, ALI, at=NOW)

    assert change is Change.ASSIGNED
    assert delivery.driver_assignment_count == 1
    assert db.active[0].name == "Ali"


# ── how far away they are ─────────────────────────────────────────────────────


def _branch() -> Branch:
    #: Al Majaz, Sharjah — the kitchen the tests measure from.
    return Branch(
        id=uuid.uuid4(),
        name="Al Majaz",
        latitude=Decimal("25.3200"),
        longitude=Decimal("55.3800"),
    )


def test_a_driver_on_the_way_has_a_distance():
    delivery = _delivery(
        driver_id="79973",
        driver_name="Ali",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW,
    )

    proximity = driver_proximity.to_pickup(delivery, _branch(), now=NOW)

    assert proximity is not None
    # ~3.5 straight-line km scaled by the measured detour factor. Asserted as a
    # range rather than a figure: the factor is a fitted constant and may be
    # refitted, and a test that pins it would fail on an improvement.
    assert 4.0 < proximity.distance_km < 7.0
    assert proximity.at == NOW


def test_a_position_of_unknown_age_is_not_quoted():
    """
    The pin without a stamp. Every booking that predates migration 112 has one,
    and "the driver is 400 m away" reads identically whether it was true twenty
    seconds or twenty minutes ago.
    """
    delivery = _delivery(
        driver_id="79973",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=None,
    )
    assert driver_proximity.to_pickup(delivery, _branch(), now=NOW) is None


def test_a_stale_position_goes_quiet_rather_than_lying():
    delivery = _delivery(
        driver_id="79973",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW - driver_proximity.MAX_AGE - timedelta(seconds=1),
    )
    assert driver_proximity.to_pickup(delivery, _branch(), now=NOW) is None


def test_no_distance_once_the_parcel_is_on_the_bike():
    """
    Past collection the driver is supposed to be getting further away, and a
    growing distance-from-the-kitchen beside a boxed order is worse than none.
    """
    delivery = _delivery(
        courier_status="PICKED_UP",
        driver_id="79973",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW,
    )
    assert driver_proximity.to_pickup(delivery, _branch(), now=NOW) is None


def test_no_distance_on_a_booking_that_ended():
    delivery = _delivery(
        courier_status="CANCELED",
        driver_id="79973",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW,
    )
    assert driver_proximity.to_pickup(delivery, _branch(), now=NOW) is None


def test_a_branch_with_no_pin_asks_nothing_of_the_maths():
    delivery = _delivery(
        driver_id="79973",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW,
    )
    branch = _branch()
    branch.latitude = None
    assert driver_proximity.to_pickup(delivery, branch, now=NOW) is None


def test_the_sweep_refreshes_before_the_counter_stops_being_told():
    """
    The two windows have to overlap, or the distance blinks out between sweeps.

    `driver_tracking` refreshes a position once it is older than `STALE_AFTER`;
    `driver_proximity` stops quoting one older than `MAX_AGE`. If the first were
    the larger, there would be a stretch of every minute in which the counter
    saw nothing and nothing was being fetched.
    """
    from app.services import driver_tracking

    assert driver_tracking.STALE_AFTER < driver_proximity.MAX_AGE


# ── the routed answer ─────────────────────────────────────────────────────────


def _routed(**overrides) -> OrderDelivery:
    """A delivery with a driver on the way and a fresh Mapbox answer on it."""
    fields = {
        "driver_id": "79973",
        "driver_name": "Ali",
        "driver_latitude": Decimal("25.3500"),
        "driver_longitude": Decimal("55.3900"),
        "driver_location_at": NOW,
        "driver_route_km": Decimal("6.4"),
        "driver_route_minutes": Decimal("11.0"),
        "driver_route_at": NOW,
    }
    fields.update(overrides)
    return _delivery(**fields)


def test_a_routed_leg_beats_the_straight_line():
    """
    The reason Mapbox is called at all.

    6.4 km of road against a straight line that would have said about 5 — the
    creek, the one-ways and the bridges are the difference, and they are the
    difference a rider actually drives.
    """
    proximity = driver_proximity.to_pickup(_routed(), _branch(), now=NOW)

    assert proximity is not None
    assert proximity.is_routed
    assert proximity.distance_km == 6.4
    assert proximity.minutes == 11.0


def test_the_fallback_offers_no_eta_at_all():
    """
    No token, an unreachable Mapbox, a pin with no drivable route — all of them
    land on the estimate, and none of them may produce minutes.

    A duration got by dividing a straight-line guess by an assumed speed is a
    guess wearing the clothes of a measurement, and a counter cannot tell the
    two apart. Nothing beats a confident wrong number.
    """
    delivery = _routed(
        driver_route_km=None, driver_route_minutes=None, driver_route_at=None
    )

    proximity = driver_proximity.to_pickup(delivery, _branch(), now=NOW)

    assert proximity is not None
    assert not proximity.is_routed
    assert proximity.minutes is None
    assert proximity.distance_km > 0


def test_a_stale_route_falls_back_rather_than_quoting_old_minutes():
    """
    The position and the route go stale for different reasons — a rider moves,
    and separately Mapbox stops answering or the token is wrong. A fresh pin
    with an old route is a real state, and "6 min away" for a road the driver
    left ten minutes ago is worse than the estimate.
    """
    delivery = _routed(
        driver_route_at=NOW - driver_proximity.MAX_AGE - timedelta(seconds=1)
    )

    proximity = driver_proximity.to_pickup(delivery, _branch(), now=NOW)

    assert proximity is not None
    assert not proximity.is_routed
    assert proximity.minutes is None


def test_a_route_is_not_quoted_once_the_parcel_is_collected():
    """The gates that applied to the estimate apply to the routed answer too."""
    delivery = _routed(courier_status="PICKED_UP")
    assert driver_proximity.to_pickup(delivery, _branch(), now=NOW) is None


@pytest.mark.asyncio
async def test_a_failed_route_leaves_the_last_good_one_alone(monkeypatch):
    """
    Mapbox says nothing — unreachable, a bad token, a pin in the sea.

    The columns keep whatever they had and, crucially, `driver_route_at` is
    **not** stamped. Stamping on failure would make a broken token look like a
    fresh route for a minute at a time, suppressing the retry — and the screens
    would quietly stop showing an ETA with nothing to say why.
    """
    from app.services import driver_routing
    from app.services.providers import mapbox_provider

    async def _no_route(**_kwargs):
        return None

    monkeypatch.setattr(mapbox_provider, "route", _no_route)

    delivery = _routed()
    moved = await driver_routing._route_one(
        _Db(), delivery, _branch(), at=NOW + timedelta(minutes=5)
    )

    assert moved is False
    assert delivery.driver_route_at == NOW
    assert delivery.driver_route_km == Decimal("6.4")


@pytest.mark.asyncio
async def test_a_successful_route_is_written_with_the_moment_it_was_asked(monkeypatch):
    from app.services import driver_routing
    from app.services.providers import mapbox_provider

    async def _leg(**_kwargs):
        return mapbox_provider.Route(distance_km=3.2, minutes=7.5)

    monkeypatch.setattr(mapbox_provider, "route", _leg)

    delivery = _routed()
    later = NOW + timedelta(minutes=5)
    assert await driver_routing._route_one(_Db(), delivery, _branch(), at=later)

    assert delivery.driver_route_km == Decimal("3.2")
    assert delivery.driver_route_minutes == Decimal("7.5")
    assert delivery.driver_route_at == later


@pytest.mark.asyncio
async def test_mapbox_is_asked_for_lng_lat_in_that_order(monkeypatch):
    """
    Their coordinate order is the reverse of every other pin in this codebase,
    and getting it wrong does not raise — it routes across the Arabian Sea and
    returns a plausible-looking number of kilometres.
    """
    from app.services import driver_routing
    from app.services.providers import mapbox_provider

    seen: dict = {}

    async def _capture(**kwargs):
        seen.update(kwargs)
        return mapbox_provider.Route(distance_km=1.0, minutes=2.0)

    monkeypatch.setattr(mapbox_provider, "route", _capture)
    await driver_routing._route_one(_Db(), _routed(), _branch(), at=NOW)

    # Latitudes are ~25 in Sharjah and longitudes ~55. A transposition shows up
    # here as the two swapping magnitude.
    assert 25 < seen["from_latitude"] < 26
    assert 55 < seen["from_longitude"] < 56
    assert 25 < seen["to_latitude"] < 26
    assert 55 < seen["to_longitude"] < 56


# ── the sweep's own query ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_sweep_builds_its_query_when_lalamove_is_configured(monkeypatch):
    """
    Reach the query. Every other test in this file stops short of it.

    This is the test that was missing, and its absence shipped a bug to
    production: `refresh_live_drivers` returns early when Lalamove is not
    configured, which it never is under test, so the `select(...)` below was
    never once executed by CI. It referenced `lalamove_service.PROVIDER` — a
    constant `noon_send_service` has and `lalamove_service` did not — and every
    tick of the sweep died on an `AttributeError` that the surrounding
    `except` dutifully logged and swallowed.

    Nothing was broken loudly. The sweep simply did nothing, forever, which is
    exactly the shape of failure the logging was meant to make visible and
    instead made survivable.

    So this asserts the cheapest possible thing — that the query can be built
    and run — because "does this module reference a name that exists" is not a
    question a reader can answer by looking, and is the only question that
    mattered here.
    """
    from app.services import driver_tracking, lalamove_service

    monkeypatch.setattr(lalamove_service, "is_enabled", lambda: True)

    executed: list = []

    class _QueryingDb(_Db):
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
            )

    refreshed = await driver_tracking.refresh_live_drivers(_QueryingDb(), now=NOW)

    assert refreshed == 0
    assert executed, "the sweep returned before it ever asked the database"
    assert str(executed[0]).startswith("SELECT")


@pytest.mark.asyncio
async def test_the_routing_sweep_builds_its_query_too(monkeypatch):
    """The same hole, on the sibling sweep. Same reason, same cheap assertion."""
    from app.services import driver_routing
    from app.services.providers import mapbox_provider

    monkeypatch.setattr(mapbox_provider, "is_configured", lambda: True)

    executed: list = []

    class _QueryingDb(_Db):
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
            )

    routed = await driver_routing.refresh_routes(_QueryingDb(), now=NOW)

    assert routed == 0
    assert executed, "the sweep returned before it ever asked the database"
    assert str(executed[0]).startswith("SELECT")
