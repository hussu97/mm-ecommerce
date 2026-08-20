"""
Who is carrying this order right now, and what changed when that changed.

**One decision for both couriers.** Lalamove and noon Send report a rider
completely differently — one sends a `driverId` on a status change and makes you
fetch the name, the other sends nothing at all and makes you fetch the whole
task — but the question underneath is identical: *is this the same person we
already told the shop about?* Answering it twice, once in each service, is how
the two ended up disagreeing about what a reassignment even is.

**The bug this exists to close.** Both services used to fill the driver only
when the row had none: `if delivery.driver_id and not had_driver` on one side,
`if status == assigned and status != current` on the other. A courier swapping
riders mid-booking — a shift ending, a bike that will not start, a job passed on
— arrives as neither of those. So the swap was applied to nothing: the row kept
the first name and the first phone number, the admin card named a driver who had
dropped the job an hour ago, and the counter rang them when a different person
was standing at the door.

**Identity is not always an id.** Lalamove has one. noon Send's `da_details` is
a name, a phone number and a position — no id anywhere — so a rider swap there
is only ever visible as the name or the number changing. `_matches` compares on
whichever of the three the courier actually gave us, in that order of trust, and
treats a payload that names nobody as "no news" rather than as a swap. Getting
this wrong in the safe direction costs a duplicate slip at the counter; getting
it wrong in the other costs a driver nobody knows about.

**Two places are written, and only from here.** `order_drivers` is the ledger —
one row per stint, exactly one of them `is_active`, enforced by a unique
constraint. `order_deliveries` carries a live copy of that active row so the
register's order list and the fifteen-second tracking webhook need no join. They
cannot drift because a single function writes both inside one transaction, which
is the same arrangement `orders.status` has with `order_status_events`.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_delivery import OrderDelivery
from app.models.order_driver import OrderDriver

logger = logging.getLogger(__name__)

__all__ = [
    "Driver",
    "Change",
    "active_driver",
    "clear",
    "record",
    "record_position",
]


@dataclass(frozen=True)
class Driver:
    """A rider as one courier or the other described them, normalised."""

    driver_id: str | None = None
    name: str | None = None
    phone: str | None = None
    plate: str | None = None

    @property
    def is_empty(self) -> bool:
        """Nothing identifying at all — a payload that named no rider."""
        return not any((self.driver_id, self.name, self.phone))

    @classmethod
    def from_lalamove(
        cls, driver: dict | None, *, driver_id: str | None = None
    ) -> Driver:
        """
        Their `driver` block, or a bare `driverId` off the order.

        Both shapes turn up: `DRIVER_ASSIGNED` carries the whole person, and the
        status change that production actually receives carries only the id —
        which is why the second argument exists rather than callers building a
        fake dict.
        """
        driver = driver or {}
        return cls(
            driver_id=_clean(driver.get("driverId") or driver_id),
            name=_clean(driver.get("name")),
            phone=_clean(driver.get("phone")),
            plate=_clean(driver.get("plateNumber")),
        )

    @classmethod
    def from_noon_send(cls, da_details: dict | None) -> Driver:
        """
        noon Send's `da_details`, which has no id in it.

        Their field is `phone_number` rather than `phone`, and the block is
        absent entirely until a rider is matched — so an empty one is the
        ordinary state of a task nobody has taken, not an error.
        """
        details = da_details or {}
        return cls(
            name=_clean(details.get("name")),
            phone=_clean(details.get("phone_number") or details.get("phone")),
            plate=_clean(details.get("vehicle_number") or details.get("plate_number")),
        )


class Change(enum.Enum):
    """What `record` did."""

    #: The payload named nobody, or named the same person we already had.
    UNCHANGED = "unchanged"
    #: The first driver on this booking.
    ASSIGNED = "assigned"
    #: A different driver has taken over from one we had already announced.
    REASSIGNED = "reassigned"

    @property
    def is_new_driver(self) -> bool:
        """Somebody at the counter has to be told, and a slip is owed."""
        return self is not Change.UNCHANGED


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _matches(delivery: OrderDelivery, driver: Driver) -> bool:
    """
    Whether *driver* is the person already on the row.

    Compared on the strongest thing both sides carry, and only that thing: a
    courier that issues ids is authoritative about them, so two different ids are
    two different people even where the names agree — riders share first names.
    Where neither side has an id the phone number is the next best handle (a
    number belongs to one person), and the name is the last resort, for a
    noon Send task whose detail arrived without one.
    """
    if driver.driver_id and delivery.driver_id:
        return driver.driver_id == delivery.driver_id
    if driver.phone and delivery.driver_phone:
        return driver.phone == delivery.driver_phone
    if driver.name and delivery.driver_name:
        return driver.name == delivery.driver_name
    # The two sides share no handle at all — a payload carrying only a name
    # against a row carrying only an id, which is what a Lalamove status push
    # looks like before the detail call has run. That is not evidence of a swap,
    # and inventing one would blank a driver the shop is relying on. Only ever
    # reached with a driver already on the row: the caller settles the
    # first-assignment case before asking.
    return True


async def active_driver(
    db: AsyncSession, delivery: OrderDelivery
) -> OrderDriver | None:
    """The ledger row for whoever is carrying this order, if anybody is."""
    return (
        (
            await db.execute(
                select(OrderDriver).where(
                    OrderDriver.order_id == delivery.order_id,
                    OrderDriver.is_active.is_(True),
                )
            )
        )
        .scalars()
        .first()
    )


async def record(
    db: AsyncSession,
    delivery: OrderDelivery,
    driver: Driver,
    *,
    at: datetime | None = None,
) -> Change:
    """
    Put *driver* on the order, and say whether that was news.

    Announcing, printing and pushing are the caller's, keyed off the answer.
    This does the bookkeeping and nothing else, because every caller is inside a
    webhook that has to return 200 or a sweep that must not die on one row.

    An empty *driver* changes nothing and returns `UNCHANGED`. Couriers send
    plenty of payloads with no rider in them, and each one used to be a chance
    to blank a name the shop was relying on.
    """
    if driver.is_empty:
        return Change.UNCHANGED

    moment = at or datetime.now(timezone.utc)
    current = await active_driver(db, delivery)
    known = bool(delivery.driver_id or delivery.driver_name or delivery.driver_phone)

    if known and _matches(delivery, driver):
        # The same person, possibly better described — Lalamove's status push
        # carries an id and nothing else, and the detail call that follows is
        # what puts a name to it. Filling the gaps is not a reassignment.
        _fill_gaps(delivery, current, driver)
        if current is None:
            # A driver on the row with no ledger behind them: a booking made
            # before this table existed, or one whose live columns an older code
            # path wrote directly. Adopt what is there as the first stint rather
            # than leaving the order with no history at all — otherwise the next
            # genuine swap would be recorded as a first assignment.
            #
            # Opened from the merged row rather than from *driver*, because this
            # payload may know less than the row does and a stint must not be
            # opened with a name blanked out.
            _open_stint(
                db, delivery, _live(delivery), at=delivery.driver_assigned_at or moment
            )
            return Change.ASSIGNED
        return Change.UNCHANGED

    if current is not None:
        # NULL, never FALSE: `uq_order_driver_active` is on (order_id, is_active)
        # and Postgres counts NULLs as distinct, which is what lets any number of
        # finished stints sit beside exactly one live one.
        current.is_active = None
        current.replaced_at = moment
        logger.info(
            "Driver swapped on order %s: %s → %s",
            delivery.order_id,
            current.name or current.courier_driver_id or "?",
            driver.name or driver.driver_id or "?",
        )

    _open_stint(db, delivery, driver, at=moment)
    return Change.REASSIGNED if known else Change.ASSIGNED


def _live(delivery: OrderDelivery) -> Driver:
    """The driver as the delivery's own columns currently describe them."""
    return Driver(
        driver_id=delivery.driver_id,
        name=delivery.driver_name,
        phone=delivery.driver_phone,
        plate=delivery.driver_plate,
    )


def _fill_gaps(
    delivery: OrderDelivery, current: OrderDriver | None, driver: Driver
) -> None:
    """Add what this payload knows and the row did not, changing nobody."""
    delivery.driver_id = driver.driver_id or delivery.driver_id
    delivery.driver_name = driver.name or delivery.driver_name
    delivery.driver_phone = driver.phone or delivery.driver_phone
    delivery.driver_plate = driver.plate or delivery.driver_plate
    if current is not None:
        current.courier_driver_id = driver.driver_id or current.courier_driver_id
        current.name = driver.name or current.name
        current.phone = driver.phone or current.phone
        current.plate = driver.plate or current.plate
        current.courier_order_id = delivery.courier_order_id or current.courier_order_id


def _open_stint(
    db: AsyncSession,
    delivery: OrderDelivery,
    driver: Driver,
    *,
    at: datetime,
) -> OrderDriver:
    """Start a new row for *driver* and point the delivery's live copy at it."""
    sequence = (delivery.driver_assignment_count or 0) + 1
    row = OrderDriver(
        order_id=delivery.order_id,
        provider=delivery.provider,
        courier_order_id=delivery.courier_order_id,
        courier_driver_id=driver.driver_id,
        name=driver.name,
        phone=driver.phone,
        plate=driver.plate,
        sequence=sequence,
        assigned_at=at,
        is_active=True,
    )
    db.add(row)

    # Overwritten wholesale rather than merged. A different person's row must not
    # keep the last one's number plate because the new payload did not mention
    # one — a half-updated driver is worse than an incomplete one, because it
    # reads as complete.
    delivery.driver_id = driver.driver_id
    delivery.driver_name = driver.name
    delivery.driver_phone = driver.phone
    delivery.driver_plate = driver.plate
    delivery.driver_assigned_at = at
    delivery.driver_assignment_count = sequence
    # The position belonged to whoever left. Cleared rather than carried, so
    # nothing quotes the old rider's distance as the new one's.
    delivery.driver_latitude = None
    delivery.driver_longitude = None
    delivery.driver_location_at = None
    return row


async def record_position(
    db: AsyncSession,
    delivery: OrderDelivery,
    *,
    latitude,
    longitude,
    at: datetime | None = None,
) -> None:
    """
    Move the current driver's pin, on the ledger row and on the live copy.

    Stamped with *at*, and that stamp is the point. A position with no age is a
    position nothing can refuse: "the driver is 400 m away" reads the same
    whether it was measured twenty seconds or twenty minutes ago, and only one
    of those is a sentence a counter should act on.

    A pair that is not fully known is dropped rather than half-applied — a
    latitude without a longitude is not a place.
    """
    if latitude is None or longitude is None:
        return
    moment = at or datetime.now(timezone.utc)
    delivery.driver_latitude = latitude
    delivery.driver_longitude = longitude
    delivery.driver_location_at = moment

    current = await active_driver(db, delivery)
    if current is not None:
        current.latitude = latitude
        current.longitude = longitude
        current.located_at = moment


async def clear(
    db: AsyncSession, delivery: OrderDelivery, *, at: datetime | None = None
) -> None:
    """
    Nobody is on this booking any more — a re-dispatch, or a fresh booking.

    Closes the open stint rather than deleting it: the driver who was assigned
    to the cancelled booking really did hold this order for a while, and that is
    what somebody is asking about when they ask who had it at four o'clock.

    `driver_assignment_count` is deliberately **not** reset. It is what the
    register compares against its own ledger to decide a driver slip is owed, so
    it has to be monotonic per order; winding it back would make the next
    driver's slip look like one already printed, and the counter would meet a
    stranger holding a bag with somebody else's name on the paper.
    """
    moment = at or datetime.now(timezone.utc)
    current = await active_driver(db, delivery)
    if current is not None:
        current.is_active = None
        current.replaced_at = moment

    delivery.driver_id = None
    delivery.driver_name = None
    delivery.driver_phone = None
    delivery.driver_plate = None
    delivery.driver_assigned_at = None
    delivery.driver_latitude = None
    delivery.driver_longitude = None
    delivery.driver_location_at = None
