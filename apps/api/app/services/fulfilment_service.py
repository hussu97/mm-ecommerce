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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.branch import Branch
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery

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

#: How long a rider takes from the counter to the door once they are holding the
#: parcel. Only ever applied to a courier we dispatch ourselves and only once
#: `picked_up_at` exists, which is the point of it: before pickup the wait is
#: dominated by the kitchen and the run schedule, and after pickup it is one
#: person driving one route. This is the "more accurate estimate once the rider
#: has the order" — measured from a real event rather than from checkout.
RIDER_TO_DOOR = timedelta(minutes=45)

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
) -> Fulfilment:
    """
    Build the customer's view of one order's fulfilment.

    Takes the ORM row rather than the response model because it needs the
    delivery record and the branch, neither of which the customer-facing order
    schema carries — and deliberately must not carry.
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

    branch = None
    if is_pickup and order.branch_id is not None:
        branch = await db.get(Branch, order.branch_id)

    estimated_at, precision = _estimate(order, delivery, stage=stage, now=now)

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
        packed_at=delivery.dispatchable_at if delivery is not None else None,
        picked_up_at=delivery.picked_up_at if delivery is not None else None,
        delivered_at=delivery.delivered_at if delivery is not None else None,
        branch=branch,
    )


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


def _estimate(
    order: Order,
    delivery: OrderDelivery | None,
    *,
    stage: str,
    now: datetime,
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
        stamp = (delivery.delivered_at if delivery is not None else None) or (
            order.updated_at
        )
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

    if stage == "on_the_way":
        # The sharpest answer we ever have: one rider, one route, measured from
        # an event the courier reported rather than from anything we assumed.
        # This is the one case that is allowed to overrule the promise, because
        # it is the only one built from a fact rather than from a schedule.
        picked_up = delivery.picked_up_at if delivery is not None else None
        if picked_up is not None and provider in _BOOKED_BY_US:
            return _local(picked_up) + RIDER_TO_DOOR, "time"
        # Out for delivery on somebody else's van — the shop marked it by hand.
        # The day is all that is ours to promise.
        return _end_of(now), "day"

    # ── still in the kitchen, or packed and waiting for a run ────────────────
    #
    # Everything below is a schedule rather than an event, and the customer has
    # already been told what that schedule means for them. Repeat what they were
    # told; do not work it out again.
    batch = delivery.batch if delivery is not None else None

    if batch is not None and batch.dispatch_at is not None:
        # On a run, which is the one thing that can move the answer without a
        # rider touching the box: an order packed after its window closed goes
        # out on the next one. `dispatch_at` is where it is actually going, so
        # it wins over what was said at checkout — a customer moved to a later
        # run needs to be told the later time, not the earlier one.
        return _local(batch.dispatch_at) + DISPATCH_TO_DOOR, "time"

    promised = _promise(order)
    if promised is not None:
        return promised

    # ── no promise on the order ──────────────────────────────────────────────
    #
    # An order written before `promised_at` existed, or one placed with no pin
    # to read a zone off. This is what every order used to get, and it is why
    # the confirmation for MM-20260805-008 said 17:25 against a checkout that
    # had said 19:00 — nothing here can know about the window that was open when
    # the customer was looking at the page.
    if provider not in _BOOKED_BY_US:
        # A third-party zone is collected on a schedule we cannot see, and it is
        # the next day whether the order came in at nine in the morning or five
        # past eleven at night. Saying "today" would be guessing with somebody
        # else's van.
        return _end_of(now + timedelta(days=1)), "day"

    if stage == "ready":
        # Packed, ours to dispatch, travelling alone. It goes now.
        return now + DISPATCH_TO_DOOR, "time"

    return _local(order.created_at) + KITCHEN_PREP + DISPATCH_TO_DOOR, "time"


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
