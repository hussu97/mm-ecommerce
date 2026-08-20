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
