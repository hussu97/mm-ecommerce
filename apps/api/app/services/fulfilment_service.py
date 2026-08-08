"""
What a customer is allowed to know about how their order reaches them.

Everything the shop knows about fulfilment lives on `OrderDelivery`, and almost
none of it is the customer's business: which courier carries the cake, what that
courier charged, the driver's name and plate. `OrderDeliveryResponse` in the
orders router is the admin's view of that row and says so in its docstring.

This module is the other view — the one that goes on the account page, the track
page and into every order email. It answers three questions and deliberately no
others:

* **When should I expect it?** Derived from the delivery method, the courier that
  serves the zone and where the order currently is, so the promise gets sharper
  as the order moves rather than repeating what checkout said at the start.
* **Where do I collect it?** The branch the customer chose, with a map link.
* **Can I watch it move?** A tracking link, when the courier gives us one and the
  parcel is actually on its way.

What it never carries: a provider name, a driver, a plate, a cost. The rule that
the storefront is not told who carries an order is kept structurally — this model
has nowhere to put it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.branch import Branch
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.courier import Courier
from app.models.delivery_batch import DeliveryBatchGroup
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.order_status_event import OrderStatusEvent

__all__ = [
    "Fulfilment",
    "estimate_state_of",
    "for_order",
    "pickup_branches",
]

TZ = ZoneInfo(DELIVERY_TIMEZONE)

#: How long after an order is confirmed the box is ready to leave the kitchen.
#:
#: A published promise rather than a measurement, and deliberately generous: the
#: cost of being an hour early is nothing, and the cost of telling somebody their
#: cake is ready when it is still in an oven is a person standing at a counter.
#: Once the order is actually packed this stops being used at all — the real
#: event has happened and there is no longer anything to estimate.
KITCHEN_PREP = timedelta(hours=2)

#: What the courier's promise already spent before the rider had the parcel.
#:
#: A promise like noon Send's hour is measured from the order being placed and
#: covers packing the box and the rider getting to the counter as well as the
#: drive. Once `picked_up_at` exists, those minutes have happened — so the
#: remaining time is the promise **minus** this, measured from the collection.
#:
#: One number rather than per courier: it is how long our own kitchen takes to
#: hand a bag over, which does not change with who is collecting it.
COLLECTION_ALLOWANCE = timedelta(minutes=10)

#: What a rider takes from counter to door when nothing better is known — a
#: courier with no configured promise at all. The real figure comes from the
#: batch group or the courier row; this is only the floor under a missing one.
RIDER_TO_DOOR = timedelta(minutes=45)

#: The hour a third-party day-promise is bounded by. Their van and their
#: schedule, so the honest shape is a date and a "by" — not an hour we picked.
THIRD_PARTY_BY_HOUR = 22

#: How long after a run leaves the kitchen the last box on it is through a door.
#: The same hour `delivery_service.DISPATCH_TO_DOOR` promises at checkout, and
#: for the same reason: the courier optimises the route after we hand it over, so
#: which stop is last is not knowable when the promise is made.
DISPATCH_TO_DOOR = timedelta(hours=1)

#: Statuses where there is nothing left to promise. A cancelled or refunded order
#: is not arriving, and inventing a time for one would be worse than silence.
_SETTLED = {
    OrderStatusEnum.CANCELLED,
    OrderStatusEnum.REFUNDED,
    OrderStatusEnum.DISPUTED,
    OrderStatusEnum.PAYMENT_FAILED,
}

#: Couriers we book ourselves, and therefore whose schedule we can speak to.
_BOOKED_BY_US = {
    FulfilmentProviderEnum.LALAMOVE.value,
    FulfilmentProviderEnum.NOON_SEND.value,
}

#: Couriers that text the customer their own tracking link once a rider has the
#: parcel. Lalamove hands us a share link instead, which we surface ourselves;
#: noon Send publishes nothing to us and messages the customer directly. Both
#: end with a live map — they differ only in who delivers the link, and this is
#: what lets the email say "check your messages" instead of showing a button
#: that does not exist.
_TRACKS_BY_SMS = {FulfilmentProviderEnum.NOON_SEND.value}


@dataclass(frozen=True)
class Fulfilment:
    """How this order reaches this customer, in terms they can act on."""

    #: `"pickup"` or `"delivery"`. The order's own method, restated here so a
    #: renderer never has to reach past this object for the one fact that
    #: decides every piece of copy around it.
    method: str
    #: Where the order is in the journey, in words that are the same for both
    #: methods where the meaning is the same:
    #:   `preparing`   — being made
    #:   `ready`       — packed; waiting at the counter, or waiting for a rider
    #:   `on_the_way`  — a rider is holding it
    #:   `collected`   — pickup, handed over
    #:   `delivered`   — delivery, through the door
    #:   `undelivered` — a rider arrived and could not hand it over
    #:   `settled`     — cancelled, refunded, disputed or unpaid
    stage: str
    #: When the customer should have it. None when there is nothing honest to
    #: say — a settled order, or a delivery whose courier we do not schedule and
    #: whose parcel has not moved yet.
    estimated_at: datetime | None
    #: `"time"` when we can name an hour, `"day"` when only the date is ours to
    #: promise, `"exact"` when the moment has already happened and this is a
    #: record rather than an estimate. The distinction is the whole value of the
    #: field: rendering a third-party delivery as "today, 14:00" would invent a
    #: precision that belongs to somebody else's schedule.
    precision: str | None
    #: The courier's own live map, when there is one and the parcel is on it.
    #: Withheld before pickup — a tracking page for a parcel nobody has
    #: collected shows an empty map and reads as a broken link.
    tracking_url: str | None
    #: Whether the customer gets their tracking link by text message rather than
    #: from us. True only once a rider is actually carrying the parcel, because
    #: that is when the message is sent and promising it earlier would have
    #: somebody checking an empty inbox.
    #:
    #: A boolean, not a courier name, for the same reason as `courier_managed`:
    #: everything reading it wants the consequence — tell them to look at their
    #: phone — and nothing that reads it should learn the brand.
    tracking_by_sms: bool
    #: Whether this order travels with a courier we book and hear back from.
    #:
    #: A boolean rather than the provider's name, on purpose: everything that
    #: reads it wants the *consequence* — that the shop will learn when a rider
    #: collects, and can promise a live map — and none of them want the brand.
    #: False for collection and for the areas a third party covers, where
    #: "packed" is the last thing anybody tells us.
    courier_managed: bool
    #: The events that have actually happened, for a timeline that shows facts
    #: rather than guesses.
    packed_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    #: The branch, for a pickup order. Null for delivery: which kitchen baked it
    #: is not something a customer needs, and showing an address they should not
    #: drive to would be actively unhelpful. The ORM row rather than a copy of
    #: it — this object never outlives the session that built it, and
    #: `PickupBranchResponse` is where it becomes a payload.
    branch: Branch | None


def estimate_state_of(order: Order) -> str:
    """The stage word for an order, from its status alone."""
    if order.status in _SETTLED:
        return "settled"
    if order.status == OrderStatusEnum.DELIVERED:
        return (
            "collected"
            if order.delivery_method == DeliveryMethodEnum.PICKUP
            else "delivered"
        )
    if order.status == OrderStatusEnum.OUT_FOR_DELIVERY:
        return "on_the_way"
    if order.status == OrderStatusEnum.UNDELIVERED:
        return "undelivered"
    if order.status == OrderStatusEnum.PACKED:
        return "ready"
    return "preparing"


async def for_order(
    db: AsyncSession,
    order: Order,
    *,
    now: datetime | None = None,
    reached: dict[str, datetime] | None = None,
) -> Fulfilment:
    """
    Build the customer's view of one order's fulfilment.

    Takes the ORM row rather than the response model because it needs the
    delivery record and the branch, neither of which the customer-facing order
    schema carries — and deliberately must not carry.

    *reached* is `reached_at`'s answer, passed in by a caller that already has
    it — `order_service.to_response` needs the same map for `status_history`,
    and running the query twice per order response would be paying twice for
    one fact.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    stage = estimate_state_of(order)
    is_pickup = order.delivery_method == DeliveryMethodEnum.PICKUP

    delivery = None
    if not is_pickup:
        delivery = (
            (
                await db.execute(
                    select(OrderDelivery)
                    .where(OrderDelivery.order_id == order.id)
                    .options(selectinload(OrderDelivery.batch))
                )
            )
            .scalars()
            .first()
        )

    # The order status is the primary source now, and this is the backstop for
    # the window between a courier reporting a failed handover and the order
    # having moved — a Lalamove POD, say, which has no `undelivered` status of
    # its own to map from.
    if delivery is not None and delivery.courier_status == "undelivered":
        stage = "undelivered"

    promise_minutes = await _promise_minutes(db, delivery)
    reached = reached if reached is not None else await reached_at(db, order)

    branch = None
    if is_pickup and order.branch_id is not None:
        branch = await db.get(Branch, order.branch_id)

    estimated_at, precision = _estimate(
        order,
        delivery,
        stage=stage,
        now=now,
        promise_minutes=promise_minutes,
        reached=reached,
    )

    return Fulfilment(
        method=order.delivery_method.value,
        stage=stage,
        estimated_at=estimated_at,
        precision=precision,
        tracking_url=_tracking_url(delivery, stage=stage),
        tracking_by_sms=(
            delivery is not None
            and delivery.provider in _TRACKS_BY_SMS
            and stage == "on_the_way"
        ),
        courier_managed=(delivery is not None and delivery.provider in _BOOKED_BY_US),
        # From the order's own history, not from `order_deliveries`.
        #
        # These three used to read `dispatchable_at`, `picked_up_at` and
        # `delivered_at` off the delivery row, which is why they were blank on
        # every order no integrated courier touched — a pickup, a third-party
        # zone, anything walked through by hand. They were also a second copy of
        # moments `order_status_events` now records, and two copies of a fact
        # are two chances to disagree. The columns are gone; this is the source.
        packed_at=reached.get(OrderStatusEnum.PACKED.value),
        picked_up_at=reached.get(OrderStatusEnum.OUT_FOR_DELIVERY.value),
        delivered_at=reached.get(OrderStatusEnum.DELIVERED.value),
        branch=branch,
    )


async def reached_at(db: AsyncSession, order: Order) -> dict[str, datetime]:
    """
    When this order reached each status it has been through.

    The **latest** row per status. An order that went undelivered and was
    re-dispatched has two `out_for_delivery` rows, and the one that describes
    where the parcel is now is the second.

    Queried rather than read off `order.status_events`: the courier webhooks
    select an order bare, and touching an unloaded collection there is a
    `MissingGreenlet` rather than a query — the failure that silently swallowed
    four customer emails on 2026-08-05.
    """
    try:
        state = sa_inspect(order)
    except NoInspectionAvailable:
        # Not an ORM row at all. The POS path and the test fixtures both hand
        # over stand-ins, and a stand-in has no history to read.
        return {}
    if state.transient or state.pending:
        # A real `Order`, but one this session has never written. Same answer.
        return {}

    rows = (
        (
            await db.execute(
                select(OrderStatusEvent.status, OrderStatusEvent.at)
                .where(OrderStatusEvent.order_id == order.id)
                .order_by(OrderStatusEvent.at)
            )
        )
        .tuples()
        .all()
    )
    return {status: at for status, at in rows}


def _tracking_url(delivery: OrderDelivery | None, *, stage: str) -> str | None:
    """
    The courier's live map, if the customer can usefully open it.

    Two guards, both of which have to hold. The parcel must be moving — a share
    link for a booking nobody has collected renders an empty map, which reads as
    a link that does not work. And the booking must be ours: a third-party zone
    has no share link at all, so there is nothing to withhold there anyway.
    """
    if delivery is None or stage not in {"on_the_way", "undelivered"}:
        return None
    if delivery.provider not in _BOOKED_BY_US:
        return None
    return delivery.share_link or None


async def _promise_minutes(
    db: AsyncSession, delivery: OrderDelivery | None
) -> timedelta | None:
    """
    How long this order's courier promises from ready to door.

    The batch group first — a run's minutes are a property of the route, and
    Dubai's 90 differs from the northern 120 on the same rate card. Then the
    courier's own figure for anything not waiting for a run. Both are the same
    numbers `delivery_promise` quotes at checkout, read from the same rows, so
    the "arriving" email cannot promise a different duration from the one the
    customer was originally shown.

    `None` when neither is configured, which the caller reads as "fall back to
    the flat rider estimate" rather than as zero.
    """
    if delivery is None:
        return None

    batch = delivery.batch
    # `getattr`, because a run predating groups has no `group_id` to read and
    # should fall through to the courier rather than raise.
    if batch is not None and getattr(batch, "group_id", None) is not None:
        group = await db.get(DeliveryBatchGroup, batch.group_id)
        if group is not None:
            return timedelta(minutes=group.delivery_minutes_after_dispatch)

    if delivery.provider:
        courier = (
            await db.execute(select(Courier).where(Courier.code == delivery.provider))
        ).scalar_one_or_none()
        if courier is not None and courier.unbatched_promise_minutes:
            return timedelta(minutes=courier.unbatched_promise_minutes)
    return None


def _estimate(
    order: Order,
    delivery: OrderDelivery | None,
    *,
    stage: str,
    now: datetime,
    promise_minutes: timedelta | None = None,
    reached: dict[str, datetime] | None = None,
) -> tuple[datetime | None, str | None]:
    """
    When the customer gets it, and how precisely we know that.

    Read top to bottom: the further down a case sits, the less the shop knows.

    Two rules decide the whole shape. Anything that has actually *happened* — a
    delivery, a rider collecting — beats anything scheduled, because it is a
    record rather than a promise. And below that, what the checkout told the
    customer beats what this function could work out now, because a promise is
    a fact about what was said and re-deriving one is how the confirmation email
    and the checkout page came to disagree.
    """
    # Nothing is coming. Say nothing.
    if stage == "settled":
        return None, None

    # It already happened. This is a record, not a promise, so it carries the
    # real stamp and a precision that says as much.
    if stage in {"collected", "delivered"}:
        stamp = (reached or {}).get(OrderStatusEnum.DELIVERED.value) or order.updated_at
        return _local(stamp), "exact"

    # A rider arrived and could not hand it over. There is no new time to give
    # until somebody arranges one, and inventing "tomorrow" here would be a
    # promise nobody has made.
    if stage == "undelivered":
        return None, None

    if order.delivery_method == DeliveryMethodEnum.PICKUP:
        if stage == "ready":
            # Packed. It is on the counter now, and the moment it got there is a
            # fact rather than an estimate.
            return _local(order.updated_at), "exact"
        return _local(order.created_at) + KITCHEN_PREP, "time"

    # ── delivery ─────────────────────────────────────────────────────────────
    provider = delivery.provider if delivery is not None else None
    # Whether this order was ever promised an hour.
    #
    # Read off the order, not off the courier carrying it now. Those used to be
    # the same question and are not any more: an admin can move a third-party
    # order onto Lalamove after it is packed, and every branch below that keys
    # on `provider in _BOOKED_BY_US` would then start answering at a sharpness
    # nobody ever offered — a customer told "Sat 9 Aug, before 10 PM" would
    # watch that become "Sat 9 Aug, 18:40" the moment a rider collected.
    #
    # A day promise is therefore a ceiling for the life of the order. Who ends
    # up driving it is ours to change; what the customer was told is not.
    promised_a_time = order.promised_precision != "day"

    if stage == "on_the_way":
        # The sharpest answer we ever have: one rider, one route, measured from
        # an event the courier reported rather than from anything we assumed.
        # This is the one case that is allowed to overrule the promise, because
        # it is the only one built from a fact rather than from a schedule —
        # but it may still only sharpen a promise that was made as a time.
        picked_up = (reached or {}).get(OrderStatusEnum.OUT_FOR_DELIVERY.value)
        if picked_up is not None and provider in _BOOKED_BY_US and promised_a_time:
            # The promise minus what it had already spent by the time the rider
            # was holding the box. A flat 45 minutes here was the same number
            # for a Dubai run and a northern one, which differ by half an hour.
            remaining = (promise_minutes or RIDER_TO_DOOR) - COLLECTION_ALLOWANCE
            arriving = _local(picked_up) + max(remaining, timedelta(minutes=5))
            # Never behind the customer reading it. A rider who is running late
            # turns an estimate into a time that has already passed, and an
            # email saying the parcel arrived twenty minutes ago is worse than
            # one saying "any moment".
            return max(arriving, _local(now) + timedelta(minutes=5)), "time"
        # Either it is on somebody else's van and the shop marked it by hand, or
        # it is on a rider we booked against a day promise. Both get the day,
        # bounded by the hour rather than left open: "before 10 PM" is a
        # commitment a customer can plan around, and "some time on Tuesday" is
        # not.
        #
        # The promised date where there is one, not today. A rider collecting an
        # order early does not move the day it was promised for, and quietly
        # re-dating it to `now` is how an order promised for tomorrow would
        # start claiming it arrives this evening.
        promised = _promise(order)
        day = promised[0] if promised is not None and not promised_a_time else now
        return _by_hour(day, THIRD_PARTY_BY_HOUR), "day_by"

    # ── still in the kitchen, or packed and waiting for a run ────────────────
    #
    # Everything below is a schedule rather than an event, and the customer has
    # already been told what that schedule means for them. Repeat what they were
    # told; do not work it out again.
    batch = delivery.batch if delivery is not None else None

    if batch is not None and batch.dispatch_at is not None and promised_a_time:
        # On a run, which is the one thing that can move the answer without a
        # rider touching the box: an order packed after its window closed goes
        # out on the next one. `dispatch_at` is where it is actually going, so
        # it wins over what was said at checkout — a customer moved to a later
        # run needs to be told the later time, not the earlier one.
        #
        # The group's own minutes, not a flat hour. A flat hour here against the
        # 90 the Dubai group promises at checkout is the same order carrying two
        # different arrival times — the disagreement `delivery_promise` exists
        # to remove, reintroduced one layer down.
        return (
            _local(batch.dispatch_at) + (promise_minutes or DISPATCH_TO_DOOR),
            "time",
        )

    promised = _promise(order)
    if promised is not None:
        at, precision = promised
        if precision == "day":
            # The date the customer was promised, bounded by the hour the
            # partner finishes. Same day they were told at checkout — this only
            # sharpens "some time on Tuesday" into "Tuesday before 10 PM", which
            # is the difference between a date and something to plan around.
            #
            # No longer conditional on the courier. It was, and a reassignment
            # to Lalamove dropped the bound while the order was still in the
            # kitchen: the same promise rendered as a bare date one minute and
            # "before 10 PM" the next, with nothing having happened to the cake.
            return _by_hour(at, THIRD_PARTY_BY_HOUR), "day_by"
        return promised

    # ── no promise on the order ──────────────────────────────────────────────
    #
    # An order written before `promised_at` existed, or one placed with no pin
    # to read a zone off. This is what every order used to get, and it is why
    # the confirmation for MM-20260805-008 said 17:25 against a checkout that
    # had said 19:00 — nothing here can know about the window that was open when
    # the customer was looking at the page.
    # `original_provider` as well as `provider`, so an order that was written
    # against a third-party zone and later moved onto Lalamove keeps the shape
    # of answer it has been giving. Without it, reassigning one of these — an
    # order predating `promised_at`, so with no stored promise to fall back on —
    # would turn "tomorrow before 10 PM" into an hour, which is the one thing
    # this whole change exists to prevent.
    was_third_party = provider not in _BOOKED_BY_US or (
        delivery is not None and delivery.original_provider not in (None, provider)
    )
    if was_third_party:
        # A third-party zone is collected on a schedule we cannot see, and it is
        # the next day whether the order came in at nine in the morning or five
        # past eleven at night. Saying "today" would be guessing with somebody
        # else's van.
        return _by_hour(now + timedelta(days=1), THIRD_PARTY_BY_HOUR), "day_by"

    if stage == "ready":
        # Packed, ours to dispatch, travelling alone. It goes now, and takes
        # this courier's own time rather than a flat hour.
        return now + (promise_minutes or DISPATCH_TO_DOOR), "time"

    return (
        _local(order.created_at) + KITCHEN_PREP + (promise_minutes or DISPATCH_TO_DOOR),
        "time",
    )


def _promise(order: Order) -> tuple[datetime, str] | None:
    """
    What the checkout told this customer, if anything.

    Read off the order rather than derived, which is the entire point: the
    window that was open when somebody was looking at the checkout page is not
    recoverable an hour later, and re-deriving would silently move them onto a
    later run nobody mentioned.

    A promise in the past is still returned. It has not stopped being what was
    said, and a late order is a thing the customer can see for themselves on the
    tracking page — replacing it with a fresh guess would quietly erase the fact
    that we are late.
    """
    if order.promised_at is None or order.promised_precision is None:
        return None
    return _local(order.promised_at), order.promised_precision


def _local(moment: datetime) -> datetime:
    """On the shop's clock, which is the clock the customer is standing on."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TZ)


def _end_of(moment: datetime) -> datetime:
    """The end of that day, local. Carries a date without implying an hour."""
    return moment.astimezone(TZ).replace(hour=23, minute=59, second=0, microsecond=0)


def _by_hour(moment: datetime, hour: int) -> datetime:
    """
    That day, bounded at an hour.

    Rendered as "before 10 PM" rather than as an appointment — see the
    `day_by` precision. The bound is what makes a third-party promise something
    a customer can plan around while still being honest that the van is not
    ours to schedule.
    """
    return moment.astimezone(TZ).replace(hour=hour, minute=0, second=0, microsecond=0)


async def pickup_branches(db: AsyncSession) -> list[Branch]:
    """
    The branches a customer may choose to collect from.

    A pin is required as well as the flag: the whole point of offering the
    choice is a map link and an address, and a branch with no coordinates can
    give neither.
    """
    return list(
        (
            await db.execute(
                select(Branch)
                .where(
                    Branch.offers_pickup.is_(True),
                    Branch.is_active.is_(True),
                    Branch.deleted_at.is_(None),
                    Branch.latitude.isnot(None),
                    Branch.longitude.isnot(None),
                )
                .order_by(Branch.display_order, Branch.name)
            )
        )
        .scalars()
        .all()
    )
