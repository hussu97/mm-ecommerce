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
from sqlalchemy.exc import IntegrityError

from app.models.branch import Branch
from app.models.order_delivery import OrderDelivery
from app.models.order_driver import OrderDriver
from app.services.delivery import driver_assignment, driver_proximity
from app.services.delivery.driver_assignment import Change, Driver

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


class _Db:
    """
    Enough session to answer "who is on this order" and take a new row.

    The ledger lives in a list rather than a database because every rule under
    test is about which row is active and what the counter is told — none of it
    is about SQL.

    **Two things it models on purpose, because the version that did not let a
    production bug through.** The real sessionmaker is built `autoflush=False`,
    so a row that has only been `add`ed is invisible to every later `SELECT`
    until something flushes; and `uq_order_driver_active` refuses a second live
    stint on one order at the moment of the flush, not at the moment of the
    `add`. The first fake had `add` append straight onto the visible list, which
    made every read here read-your-own-writes and every double-open impossible —
    so ten green tests sat on top of a `record()` that opened two stints on one
    order and took the whole request down at commit.
    """

    def __init__(self, drivers: list[OrderDriver] | None = None) -> None:
        self.drivers: list[OrderDriver] = list(drivers or [])
        self.pending: list[OrderDriver] = []
        self.flushes = 0

    def add(self, row) -> None:
        # Held back, not published. This is `autoflush=False`.
        self.pending.append(row)

    async def flush(self) -> None:
        self.flushes += 1
        self.drivers.extend(self.pending)
        self.pending.clear()
        live = [d for d in self.drivers if d.is_active]
        by_order: dict[uuid.UUID, int] = {}
        for row in live:
            by_order[row.order_id] = by_order.get(row.order_id, 0) + 1
        clash = [oid for oid, n in by_order.items() if n > 1]
        if clash:
            # What Postgres would raise, at the point it would raise it.
            raise IntegrityError(
                "INSERT INTO order_drivers",
                {},
                Exception(
                    "duplicate key value violates unique constraint "
                    f'"uq_order_driver_active" (order_id={clash[0]}, is_active=t)'
                ),
            )

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
    from app.services.delivery import driver_tracking

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
    from app.services.delivery import driver_routing
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
    from app.services.delivery import driver_routing
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
    from app.services.delivery import driver_routing
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
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking

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
    from app.services.delivery import driver_routing
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


# ── the double open ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_naming_the_driver_we_just_recorded_does_not_open_a_second_stint():
    """
    MM-20260821-001, 21 August 2026, and the shape of it matters more than the
    order number.

    Lalamove's status push carries a bare `driverId`. `record` opens a stint for
    it; because that is news, the webhook then calls `fill_driver_details` to put
    a name to the id, and the answer comes back through `record` a second time —
    same person, better described. In the same session, with no flush between.

    The second call used to read no active driver, because `autoflush=False`
    keeps the first stint out of its own `SELECT`, and took that as "a driver on
    the row with no ledger behind them" — the adoption branch, meant for
    bookings older than this table. It opened a stint of its own. Both rows met
    at the commit `get_db` does after the route has already answered 200, so
    Lalamove logged a delivered webhook, the transaction rolled back whole, and
    the order kept no driver and a `courier_status` frozen where it stood.

    Once per assignment, whatever the payload arrives as.
    """
    db, delivery = _Db(), _delivery(courier_status="ASSIGNING_DRIVER")

    bare = Driver(driver_id="4827243", name=None, phone=None, plate=None)
    assert await driver_assignment.record(db, delivery, bare, at=NOW) is Change.ASSIGNED

    named = Driver(
        driver_id="4827243",
        name="Ali Gohar Muhammad Idrees",
        phone="+971527334372",
        plate="**3182*",
    )
    # The same person the row already holds, so nothing is news.
    assert (
        await driver_assignment.record(db, delivery, named, at=NOW) is Change.UNCHANGED
    )

    await db.flush()  # the commit that used to blow up

    assert len(db.active) == 1
    assert db.active[0].sequence == 1
    assert delivery.driver_assignment_count == 1
    # The second payload knew more, and the row kept what it learned.
    assert delivery.driver_name == "Ali Gohar Muhammad Idrees"
    assert delivery.driver_phone == "+971527334372"


@pytest.mark.asyncio
async def test_a_stint_is_visible_to_the_next_read_in_its_own_session():
    """
    The invariant underneath the test above, stated on its own.

    `record` decides what to do by asking `active_driver`, so a stint it has
    just opened has to be something that question can see. Without the flush it
    is not, and every caller that records twice before a commit — the Lalamove
    webhook, `driver_tracking`'s sweep, anything reached through
    `fill_driver_details` — silently gets the adoption branch.
    """
    db, delivery = _Db(), _delivery()

    await driver_assignment.record(db, delivery, ALI, at=NOW)

    assert db.flushes >= 1
    assert await driver_assignment.active_driver(db, delivery) is not None


@pytest.mark.asyncio
async def test_a_swap_never_has_two_live_stints_at_the_flush():
    """
    The other way to land two active rows on one order.

    Closing the old stint and opening the new one are an UPDATE and an INSERT,
    and SQLAlchemy emits inserts before updates within a flush. Left to one
    flush the new row lands while the old one is still `is_active = true`, which
    is the exact pair the constraint exists to refuse. The close is flushed
    first, so the slot is free before anything takes it.
    """
    db, delivery = _Db(), _delivery()

    await driver_assignment.record(db, delivery, ALI, at=NOW)
    await driver_assignment.record(db, delivery, SAMEER, at=NOW + timedelta(minutes=5))
    await db.flush()

    assert len(db.active) == 1
    assert db.active[0].courier_driver_id == "80112"
    assert len(db.drivers) == 2
    assert delivery.driver_assignment_count == 2


# ── the sweep's blind spot ────────────────────────────────────────────────────


def _compiled(stmt) -> str:
    """The SELECT as Postgres would receive it, literals and all."""
    return str(
        stmt.compile(
            dialect=__import__(
                "sqlalchemy.dialects.postgresql", fromlist=["dialect"]
            ).dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.asyncio
async def test_the_sweep_looks_at_bookings_with_no_driver_on_them(monkeypatch):
    """
    The backstop used to be keyed off the value the failure destroys.

    `refresh_live_drivers` asked for `driver_id IS NOT NULL`, so a booking whose
    driver write was lost — the only kind that needs recovering — was the exact
    kind it filtered out. MM-20260821-001 sat through its whole delivery inside
    that gap: nothing would look at it again, and every later webhook took the
    same doomed path.

    `_refresh_one` reads the booking before the driver and learns the id from
    it, so the filter never bought anything either.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking

    monkeypatch.setattr(lalamove_service, "is_enabled", lambda: True)

    executed: list = []

    class _QueryingDb(_Db):
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
            )

    await driver_tracking.refresh_live_drivers(_QueryingDb(), now=NOW)

    sql = _compiled(executed[0])
    assert "driver_id IS NOT NULL" not in sql
    # And the status it was stuck in is one the sweep now asks about.
    assert "ASSIGNING_DRIVER" in sql
    assert "ON_GOING" in sql


@pytest.mark.asyncio
async def test_the_sweep_stops_chasing_a_booking_that_is_old_enough_to_be_somebody_else(
    monkeypatch,
):
    """
    The cost of opening the filter, bounded.

    A row leaves `_LIVE_LALAMOVE` only when something writes a later status onto
    it, and the sweep leaves terminal transitions to the webhook — so a booking
    whose `COMPLETED` push was lost would otherwise be polled once a minute
    forever.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking

    monkeypatch.setattr(lalamove_service, "is_enabled", lambda: True)

    executed: list = []

    class _QueryingDb(_Db):
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [], first=lambda: None)
            )

    await driver_tracking.refresh_live_drivers(_QueryingDb(), now=NOW)

    sql = _compiled(executed[0])
    # Rendered the way Postgres receives it: a space, not an ISO "T".
    horizon = (NOW - driver_tracking.CHASE_FOR).strftime("%Y-%m-%d %H:%M:%S")
    assert "booked_at" in sql
    assert horizon in sql, f"expected the six-hour horizon {horizon} in:\n{sql}"


@pytest.mark.asyncio
async def test_the_sweep_recovers_a_driver_the_webhook_dropped(monkeypatch):
    """
    The whole point, end to end: a booking we believe has nobody on it, which
    Lalamove says has been ON_GOING with a rider for some time.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking
    from app.services.providers import lalamove_provider

    delivery = _delivery(
        courier_status="ASSIGNING_DRIVER",
        driver_id=None,
        driver_name=None,
        driver_assignment_count=0,
    )
    db = _Db()

    async def _order(_id):
        return {"data": {"status": "ON_GOING", "driverId": "4827243"}}

    filled: list = []

    async def _fill(_db, d, *, at=None):
        filled.append(d)
        d.driver_name = "Ali Gohar Muhammad Idrees"

    monkeypatch.setattr(lalamove_provider.provider, "get_order", _order)
    monkeypatch.setattr(lalamove_service, "fill_driver_details", _fill)
    monkeypatch.setattr(lalamove_service, "announce_driver", lambda *a, **k: _noop())

    assert await driver_tracking._refresh_one(db, delivery, at=NOW) is True
    await db.flush()

    assert delivery.driver_id == "4827243"
    assert delivery.driver_assignment_count == 1
    assert len(db.active) == 1
    # The status came back with the driver, and had been frozen without it.
    assert delivery.courier_status == "ON_GOING"
    assert delivery.courier_previous_status == "ASSIGNING_DRIVER"
    assert delivery.status_updated_at == NOW
    assert filled, "the shop still needs a name, not just an id"


@pytest.mark.asyncio
async def test_a_booking_still_being_matched_costs_one_call_and_no_more(monkeypatch):
    """
    The ordinary `ASSIGNING_DRIVER` case, now that it is swept.

    There is genuinely nobody on it. The sweep must not go on to ask the driver
    endpoint about an id it does not have, or announce a rider to the counter.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking
    from app.services.providers import lalamove_provider

    delivery = _delivery(
        courier_status="ASSIGNING_DRIVER", driver_id=None, driver_assignment_count=0
    )
    db = _Db()

    async def _order(_id):
        return {"data": {"status": "ASSIGNING_DRIVER", "driverId": None}}

    async def _boom(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("asked about a driver that does not exist")

    monkeypatch.setattr(lalamove_provider.provider, "get_order", _order)
    monkeypatch.setattr(lalamove_service, "fill_driver_details", _boom)

    assert await driver_tracking._refresh_one(db, delivery, at=NOW) is False
    await db.flush()

    assert delivery.driver_id is None
    assert db.active == []


async def _noop():
    return None


# ── the ending nobody was told about ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_lost_completed_still_delivers_the_order(monkeypatch):
    """
    The gap under the gap.

    Terminal transitions belong to the webhook, and that is right until the
    webhook does not come — and then it owns nothing. A `COMPLETED` lost the way
    MM-20260821-001's driver push was lost leaves the cake in the customer's
    hands and the order at `packed`: wrong in the reports, wrong in the
    customer's timeline, and with no retry from Lalamove to wait for.

    This asserts the wiring only — that the ending goes through `apply_webhook`
    rather than being reimplemented here, carrying the delivery already in hand.
    The claim that actually matters, that the *order* reaches `delivered`, is
    made against the real `apply_webhook` in
    `test_lalamove_service.test_a_completed_nobody_pushed_still_delivers_the_order`;
    a mock cannot make it and should not appear to.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking
    from app.services.providers import lalamove_provider

    delivery = _delivery(
        courier_status="ON_GOING",
        driver_id="4827243",
        driver_name="Ali",
        driver_assignment_count=1,
    )

    applied: list = []

    async def _order(_id):
        return {"data": {"status": "COMPLETED", "driverId": "4827243"}}

    async def _apply(_db, payload, d=None):
        applied.append((payload, d))
        return d

    monkeypatch.setattr(lalamove_provider.provider, "get_order", _order)
    monkeypatch.setattr(lalamove_service, "apply_webhook", _apply)

    assert await driver_tracking._refresh_one(_Db(), delivery, at=NOW) is True

    assert applied, "the ending was noticed and then dropped on the floor"
    payload, passed = applied[0]
    # Through the same door a push uses, carrying the delivery it already holds
    # so `apply_webhook` does not go looking for it again.
    assert passed is delivery
    assert payload["data"]["order"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_an_ending_we_already_recorded_costs_nothing(monkeypatch):
    """
    The ordinary case: the push arrived, did its work, and this tick is simply
    the one that noticed. Reapplying it would rewrite `last_payload` with a
    fabricated body and re-run the consequences of a transition already made.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking
    from app.services.providers import lalamove_provider

    delivery = _delivery(courier_status="COMPLETED", driver_id="4827243")

    async def _order(_id):
        return {"data": {"status": "COMPLETED", "driverId": "4827243"}}

    async def _boom(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("reapplied an ending that was already recorded")

    monkeypatch.setattr(lalamove_provider.provider, "get_order", _order)
    monkeypatch.setattr(lalamove_service, "apply_webhook", _boom)

    assert await driver_tracking._refresh_one(_Db(), delivery, at=NOW) is False


@pytest.mark.asyncio
async def test_a_lost_pickup_is_reconciled_too(monkeypatch):
    """
    `PICKED_UP` is collected rather than terminal, and it moves the order to
    `out_for_delivery`. Losing it strands the customer's timeline just as badly,
    so it goes through the same door.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking
    from app.services.providers import lalamove_provider

    delivery = _delivery(courier_status="ON_GOING", driver_id="4827243")

    applied: list = []

    async def _order(_id):
        return {"data": {"status": "PICKED_UP", "driverId": "4827243"}}

    async def _apply(_db, payload, d=None):
        applied.append(payload)
        return d

    monkeypatch.setattr(lalamove_provider.provider, "get_order", _order)
    monkeypatch.setattr(lalamove_service, "apply_webhook", _apply)

    assert await driver_tracking._refresh_one(_Db(), delivery, at=NOW) is True
    assert applied[0]["data"]["order"]["status"] == "PICKED_UP"


def test_the_fabricated_payload_does_not_borrow_a_courier_event_name():
    """
    Their event names carry promises about the body — `DRIVER_ASSIGNED` means a
    whole person is described, `POD_STATUS_CHANGED` means a proof is attached —
    and the order endpoint makes neither. Borrowing one to get past a branch
    would put a lie in `last_payload`, which is the first thing anybody reads
    when asking what the courier actually said.
    """
    from app.models.order_delivery import CourierStatusEnum
    from app.services.delivery import driver_tracking

    payload = driver_tracking._as_payload({"status": "COMPLETED"}, at=NOW)

    assert payload["eventType"] not in {
        "DRIVER_ASSIGNED",
        "ORDER_STATUS_CHANGED",
        "POD_STATUS_CHANGED",
        "ORDER_REPLACED",
        "WALLET_BALANCE_CHANGED",
    }
    assert "POLL" in payload["eventType"].upper()
    # Readable by `webhook_time`, which takes an epoch and not a string.
    assert payload["timestamp"] == NOW.timestamp()
    assert CourierStatusEnum.COMPLETED.value == payload["data"]["order"]["status"]


@pytest.mark.asyncio
async def test_the_fabricated_payload_clears_the_out_of_order_guard(monkeypatch):
    """
    `apply_webhook` drops anything stamped earlier than the last update it
    applied, so a reconciliation stamped with the courier's own moment could be
    refused by the very guard that protects the order — the genuine event may
    well predate a later push we did receive. Stamped with now, it is always
    the newest thing we know.
    """
    from app.services.couriers import lalamove_service
    from app.services.delivery import driver_tracking

    delivery = _delivery(
        courier_status="ON_GOING",
        status_updated_at=NOW - timedelta(minutes=1),
    )
    payload = driver_tracking._as_payload({"status": "COMPLETED"}, at=NOW)

    assert lalamove_service.webhook_time(payload) == NOW
    assert lalamove_service.webhook_time(payload) > delivery.status_updated_at
