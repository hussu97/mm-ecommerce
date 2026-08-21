"""
The bridge between an order and a Lalamove booking.

Three rules shape everything here.

**A courier failure is never a customer failure.** Quoting is best-effort and
booking is out of band: if Lalamove is down, slow, unfunded or refuses the
address, the order still exists, is still paid for and is still priced from the
zone map. What breaks is dispatch, and that surfaces to an admin rather than to
a shopper.

**The price the customer pays does not come from here.** The zone map decides
the fee. The courier quote is recorded next to it and never shown, so the gap
between what we charge and what we pay is measurable rather than assumed.

**A third-party zone behaves exactly as it did before.** No call is made, no
booking exists, and the order moves by hand. The only difference is that a
delivery row is written for it too, so reporting covers the whole country.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings

# E.164 or nothing. Re-exported below, because it stopped being a courier
# concern: the same normalisation now decides whether two orders belong to one
# customer for the new-customer coupon.
from app.core.phone import normalise_phone
from app.models.branch import Branch
from app.models.cart import Cart
from app.models.delivery_batch import DeliveryBatch
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.order_delivery import (
    FAILED_COURIER_STATUSES,
    CourierStatusEnum,
    OrderDelivery,
)
from app.models.webhook_event import WebhookEvent
from app.services import (
    address_format,
    courier_reference,
    driver_assignment,
    driver_routing,
    email_service,
    order_lifecycle,
)
from app.services.delivery_zone_service import Zone
from app.services.providers.lalamove_provider import LalamoveError, provider

logger = logging.getLogger(__name__)

#: This module's provider key, as `order_deliveries.provider` spells it.
#:
#: Exists because `noon_send_service.PROVIDER` does, and code that sweeps both
#: couriers has to name them the same way. Its absence was not harmless: the
#: driver sweep referenced `lalamove_service.PROVIDER` on the reasonable
#: assumption that the two services were symmetrical, and every tick of it died
#: on an AttributeError for a fortnight of ticks — caught and logged, so batches
#: were never at risk, and silently doing nothing, which is the failure mode
#: nobody notices.
PROVIDER = FulfilmentProviderEnum.LALAMOVE.value

__all__ = [
    "Estimate",
    "PROVIDER",
    "PickupPoint",
    "Drop",
    "QuotationExpired",
    "ReassignQuote",
    "apply_price",
    "apply_webhook",
    "build_drop",
    "assign_and_dispatch",
    "cancel_delivery",
    "clear_caches",
    "courier_order_id_of",
    "decimal_or_none",
    "delivery_key_of",
    "dispatch_order",
    "estimate_for_point",
    "announce_driver",
    "fill_driver_details",
    "get_delivery",
    "handle_webhook",
    "is_enabled",
    "normalise_phone",
    "parse_quotation",
    "parse_time",
    "quote_for_order",
    "replaced_order_id_of",
    "webhook_time",
    "record_cart_estimate",
    "record_order_delivery",
    "resolve_pickup",
    "special_requests",
]


def is_enabled() -> bool:
    """Whether we can talk to a courier at all.

    Without credentials a `lalamove` zone still prices and sells normally — it
    just dispatches the way a third-party zone does.
    """
    return provider.is_configured


# ── pickup ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PickupPoint:
    """
    A branch, in the shape a courier needs it.

    Shared by both couriers rather than one each, because "which place does this
    order leave from" has a single answer and duplicating the resolution is how
    the two would come to disagree about it.
    """

    name: str
    phone: str
    address: str
    latitude: float
    longitude: float
    #: Which branch this is, for logging and for a courier that needs to name it.
    reference: str = ""
    #: The emirate this kitchen stands in, off `branches.city`. Carried because
    #: one courier's vehicle rule turns on whether the drop is in the *same*
    #: emirate as the pickup — a bike may not cross a boundary — and deriving
    #: that from the address line would be guessing at the last comma-separated
    #: fragment, which is exactly what `branches.city` exists to avoid.
    emirate: str = ""
    #: What noon Send calls this branch. Empty means it is not registered with
    #: them, so it can be a Lalamove pickup but not a noon Send one. Lalamove
    #: needs no equivalent — it takes coordinates and an address.
    noon_send_outlet_code: str = ""
    #: The exact address revision of that outlet, as
    #: `addr::restaurant_outlet::ae::CODE::2`. Optional on their side and sent
    #: only when present: the code alone identifies the outlet, and the trailing
    #: revision goes stale the moment noon edits the address.
    noon_send_outlet_address_code: str = ""
    #: The branch's trading hours as "HH:MM", carried because a driver can only
    #: collect from a kitchen that is open. A retry is not worth scheduling for
    #: a moment when nobody will be there to hand the boxes over.
    opens_at: str = "00:00"
    closes_at: str = "23:59"

    def as_stop(self) -> dict[str, Any]:
        return {
            "coordinates": {
                "lat": f"{self.latitude:.7f}",
                "lng": f"{self.longitude:.7f}",
            },
            "address": self.address,
        }


async def resolve_pickup(
    db: AsyncSession, branch_id: uuid.UUID | None = None
) -> PickupPoint | None:
    """
    The branch the courier collects from.

    `branch_id` is the zone's own kitchen, and it wins when given: the shape
    that priced the order is the shape that says who bakes it, so a second
    kitchen serving its own area needs no other change. The caller passes
    `zone.branch_id`, which is null only for a map drawn before zones knew about
    branches.

    Without one — a pickup order, or a pin outside every drawn shape — it is the
    first active branch flagged `receives_online_orders` that has a pin, because
    a branch without coordinates cannot be a pickup stop no matter how it is
    flagged. Ties break on `display_order` then name.

    Every fact used here comes off the branch row. It used to be possible to
    override the choice and the phone number from the environment, which meant
    the answer to "where does this leave from, and who does the driver call"
    lived in two places that could disagree — and the environment copy won.
    Both are columns in the admin now.
    """
    stmt = select(Branch).where(
        Branch.is_active.is_(True),
        Branch.deleted_at.is_(None),
        Branch.latitude.isnot(None),
        Branch.longitude.isnot(None),
    )
    if branch_id is not None:
        stmt = stmt.where(Branch.id == branch_id)
    else:
        stmt = stmt.where(Branch.receives_online_orders.is_(True))

    branch = (
        (await db.execute(stmt.order_by(Branch.display_order, Branch.name)))
        .scalars()
        .first()
    )
    if branch is None:
        if branch_id is not None:
            # The zone names a branch that is gone, deleted or has lost its pin.
            # Falling through to the global default would silently bake the order
            # in the wrong city, so this is a refusal rather than a guess.
            logger.warning(
                "Zone points at branch %s, which cannot be a pickup point", branch_id
            )
        return None

    phone = normalise_phone(branch.phone or "")
    if not phone:
        # Fixable in one place — the branch row — rather than by a deploy.
        logger.warning(
            "Branch %s has no phone number; the courier needs one for the sender",
            branch.reference,
        )
        return None

    return PickupPoint(
        name=branch.name,
        phone=phone,
        address=branch.address or branch.name,
        latitude=float(branch.latitude),
        longitude=float(branch.longitude),
        reference=branch.reference,
        emirate=(branch.city or "").strip(),
        # Off the branch and nowhere else. A second kitchen is a row in the
        # admin — `scripts/register_noon_send_pickup.py` writes the code there.
        noon_send_outlet_code=branch.noon_send_outlet_code or "",
        noon_send_outlet_address_code=branch.noon_send_outlet_address_code or "",
        opens_at=branch.opening_from,
        closes_at=branch.opening_to,
    )


# ── estimates ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Estimate:
    cost: Decimal
    currency: str
    distance_m: int | None
    quotation_id: str | None


#: Checkout re-quotes whenever the pin or the basket moves, and the basket does
#: not change the price of a courier run. Keyed on the origin and the rounded
#: destination so nudging the pin a few metres does not spend another call.
_quote_cache: dict[
    tuple[uuid.UUID | None, float, float],
    tuple[float, Estimate | None, str | None],
] = {}

#: A failure is cached far more briefly than a price. Outside the fixed-fee
#: zones this answer *is* the fee, so a cached failure is a customer being told
#: we do not deliver to their street — and holding that for two minutes after
#: the courier came back would turn a blip into a lost order. Long enough to
#: stop a re-render storm hammering an API that is already unhappy; short enough
#: that reloading the page is a real retry.
_FAILURE_CACHE_SECONDS = 15


def clear_caches() -> None:
    _quote_cache.clear()


async def estimate_for_point(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    address: str | None = None,
    branch_id: uuid.UUID | None = None,
) -> tuple[Estimate | None, str | None]:
    """
    What a courier would charge to reach this point, or why it could not say.

    Returns `(estimate, error)` and never raises. In a fixed-fee zone this is
    margin data and a failure costs nothing. Outside one it is the price, and a
    failure is the customer being told their address cannot be delivered to —
    so the two halves of the answer are cached on very different clocks.
    """
    if not is_enabled():
        return None, None

    # ~11 m of precision. Finer than a building, coarser than the jitter a
    # dragged pin produces. Keyed on the origin too: the same doorstep costs a
    # different amount from a different kitchen, and a shared key would serve
    # one branch's price for another's run.
    key = (branch_id, round(latitude, 4), round(longitude, 4))
    cached = _quote_cache.get(key)
    if cached:
        age = time.monotonic() - cached[0]
        ttl = (
            settings.LALAMOVE_QUOTE_CACHE_SECONDS
            if cached[1] is not None
            else _FAILURE_CACHE_SECONDS
        )
        if age < ttl:
            return cached[1], cached[2]

    pickup = await resolve_pickup(db, branch_id)
    if pickup is None:
        return None, "No pickup branch is configured"

    estimate: Estimate | None = None
    error: str | None = None
    try:
        quotation = await provider.create_quotation(
            [
                pickup.as_stop(),
                {
                    "coordinates": {
                        "lat": f"{latitude:.7f}",
                        "lng": f"{longitude:.7f}",
                    },
                    "address": address or f"{latitude:.5f}, {longitude:.5f}",
                },
            ],
            special_requests=special_requests(),
            # The full budget. This used to be capped at four seconds on the
            # grounds that a quote we would not show was not worth waiting for —
            # but outside the fixed-fee zones it *is* the price, and giving up
            # early there does not cost a second of latency, it costs the sale.
            timeout=settings.LALAMOVE_TIMEOUT_SECONDS,
        )
        estimate = parse_quotation(quotation)
        if estimate is None:
            error = "Courier returned no price"
    except LalamoveError as exc:
        error = (
            "Address is outside the courier's service area"
            if exc.is_out_of_service_area
            else str(exc)
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Unexpected error quoting Lalamove")
        error = f"Courier quote failed: {exc}"

    _quote_cache[key] = (time.monotonic(), estimate, error)
    return estimate, error


async def record_cart_estimate(
    db: AsyncSession,
    cart: Cart,
    *,
    zone: Zone | None,
    fee: Decimal,
    latitude: float,
    longitude: float,
    estimate: Estimate | None,
    error: str | None,
) -> None:
    """Park the estimate on the basket. Written even when it failed."""
    cart.delivery_quote_provider = (
        zone.fulfilment_provider if zone else FulfilmentProviderEnum.LALAMOVE.value
    )
    cart.delivery_quote_zone = zone.name if zone else None
    cart.delivery_quote_fee = fee
    cart.delivery_quote_cost = estimate.cost if estimate else None
    cart.delivery_quote_currency = estimate.currency if estimate else None
    cart.delivery_quote_distance_m = estimate.distance_m if estimate else None
    cart.delivery_quote_reference = estimate.quotation_id if estimate else None
    cart.delivery_quote_latitude = Decimal(str(round(latitude, 6)))
    cart.delivery_quote_longitude = Decimal(str(round(longitude, 6)))
    cart.delivery_quote_at = datetime.now(timezone.utc)
    cart.delivery_quote_error = error


async def record_order_delivery(
    db: AsyncSession,
    order: Order,
    *,
    zone: Zone | None,
    cart: Cart | None,
) -> OrderDelivery:
    """
    Open the delivery record as the order is written.

    Third-party zones get one as well. Their row simply never gains a courier
    id, which is the honest representation of "someone drove it there and we
    have no telemetry".

    An order that matched no zone at all still books itself. Every drawn zone is
    now carried by Lalamove, so "outside the map" describes a gap in the map
    rather than a different way of delivering, and the fee it was priced at came
    from the courier in the first place.
    """
    delivery = OrderDelivery(
        order_id=order.id,
        provider=(
            zone.fulfilment_provider if zone else FulfilmentProviderEnum.LALAMOVE.value
        ),
        zone_name=zone.name if zone else None,
        # The row, not just the name: batching needs to reach this zone's
        # schedule, and matching on a name would break the first time one is
        # renamed.
        polygon_id=zone.id if zone else None,
        fee_charged=Decimal(str(order.delivery_fee or 0)),
    )
    # The estimate the shopper's own basket collected a moment ago, carried
    # across rather than re-quoted: it is the number that described this order.
    if cart is not None and cart.delivery_quote_cost is not None:
        delivery.quoted_cost = cart.delivery_quote_cost
        delivery.quoted_currency = cart.delivery_quote_currency
        delivery.quoted_distance_m = cart.delivery_quote_distance_m
        delivery.quotation_id = cart.delivery_quote_reference
        delivery.quoted_at = cart.delivery_quote_at
    elif cart is not None and cart.delivery_quote_error:
        delivery.last_error = cart.delivery_quote_error

    db.add(delivery)
    await db.flush()
    return delivery


# ── dispatch ──────────────────────────────────────────────────────────────────


async def get_delivery(db: AsyncSession, order_id: uuid.UUID) -> OrderDelivery | None:
    result = await db.execute(
        select(OrderDelivery).where(OrderDelivery.order_id == order_id)
    )
    return result.scalars().first()


@dataclass(frozen=True)
class Drop:
    """One customer's stop on a route, in the shape the courier wants it."""

    order_number: str
    stop: dict[str, Any]
    name: str
    phone: str
    remarks: str

    def recipient(self, stop_id: str | None) -> dict[str, Any]:
        return {
            "stopId": stop_id,
            "name": self.name,
            "phone": self.phone,
            "remarks": self.remarks,
        }


def build_drop(
    order: Order, reference: str | None = None
) -> tuple[Drop | None, str | None]:
    """
    Turn an order's shipping snapshot into a stop, or say why it cannot be one.

    Returns `(drop, reason)`. The reason is written to the delivery row so an
    admin sees "no reachable phone number" rather than a courier error about a
    field they have never heard of.
    """
    address = order.shipping_address_snapshot or {}
    latitude, longitude = _coordinates(address)
    if latitude is None or longitude is None:
        return None, "Order has no delivery coordinates"

    phone = normalise_phone(str(address.get("phone") or ""))
    if not phone:
        return None, "Order has no reachable phone number"

    return (
        Drop(
            order_number=order.order_number,
            stop={
                "coordinates": {"lat": f"{latitude:.7f}", "lng": f"{longitude:.7f}"},
                "address": _drop_address(address),
            },
            name=_recipient_name(address) or order.order_number,
            phone=phone,
            remarks=_remarks(order, address, reference),
        ),
        None,
    )


async def dispatch_order(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Book a courier for one order, on its own.

    This is the un-batched path: the zone has no schedule, or nothing in the
    schedule covered the moment the box was ready. Quotes afresh rather than
    reusing the checkout estimate — quotations expire after five minutes and an
    order placed this morning is not going out on this morning's price.
    Failures are written to the delivery row and returned quietly, because the
    order is already paid for and refusing to change its status when a courier
    is unreachable helps nobody.
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None:
        return None
    if delivery.provider != FulfilmentProviderEnum.LALAMOVE.value:
        return delivery
    if (
        delivery.courier_order_id
        and delivery.courier_status not in FAILED_COURIER_STATUSES
    ):
        # Already out with someone. Re-booking would put two drivers on one cake.
        return delivery
    if not is_enabled():
        delivery.last_error = "Courier is not configured; dispatch this order by hand"
        return delivery

    # Seven digits for the driver's notes, instead of `MM-20260805-007`. Falls
    # back to the order number rather than blocking a dispatch over a label.
    reference = await courier_reference.assign(db, delivery)

    drop, reason = build_drop(order, reference)
    if drop is None:
        delivery.last_error = reason
        return delivery

    pickup = await resolve_pickup(db, order.branch_id)
    if pickup is None:
        delivery.last_error = "No pickup branch is configured"
        return delivery

    try:
        quotation = await provider.create_quotation(
            [pickup.as_stop(), drop.stop],
            special_requests=special_requests(),
        )
        stops = quotation.get("stops") or []
        if len(stops) < 2:
            raise LalamoveError("Courier quote came back without stops")

        booking = await provider.place_order(
            quotation_id=quotation.get("quotationId", ""),
            sender={
                "stopId": stops[0].get("stopId"),
                "name": pickup.name,
                "phone": pickup.phone,
            },
            recipients=[drop.recipient(stops[1].get("stopId"))],
            is_pod_enabled=True,
            # Comes back on every webhook, so a status update can find its way
            # home even if we somehow lost the courier id.
            metadata={
                "order_number": order.order_number,
                "order_id": str(order.id),
                # The short one too, so a driver quoting seven digits can be
                # matched back from their side as well as ours.
                "courier_reference": reference or "",
            },
        )
    except LalamoveError as exc:
        delivery.last_error = str(exc)
        logger.warning("Lalamove dispatch failed for %s: %s", order.order_number, exc)
        return delivery
    except Exception as exc:  # pragma: no cover — defensive
        delivery.last_error = f"Courier dispatch failed: {exc}"
        logger.exception("Unexpected error dispatching %s", order.order_number)
        return delivery

    if delivery.courier_order_id:
        delivery.previous_courier_order_ids = [
            *(delivery.previous_courier_order_ids or []),
            delivery.courier_order_id,
        ]

    estimate = parse_quotation(quotation)
    if estimate is not None:
        delivery.quoted_cost = estimate.cost
        delivery.quoted_currency = estimate.currency
        delivery.quoted_distance_m = estimate.distance_m
        delivery.quotation_id = estimate.quotation_id
        delivery.quoted_at = datetime.now(timezone.utc)

    delivery.courier_order_id = booking.get("orderId")
    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = (
        booking.get("status") or CourierStatusEnum.ASSIGNING_DRIVER.value
    )
    delivery.share_link = booking.get("shareLink")
    # A fresh booking carries nobody — Lalamove answers `ASSIGNING_DRIVER` and
    # matches a driver minutes later. `clear` closes any stint left open by the
    # booking this replaces, so a re-dispatch does not leave two people reading
    # as current, and keeps the assignment counter monotonic so the register
    # cannot mistake the next driver's slip for one already printed.
    await driver_assignment.clear(db, delivery)
    await driver_assignment.record(
        db,
        delivery,
        driver_assignment.Driver.from_lalamove(None, driver_id=booking.get("driverId")),
    )
    delivery.booked_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.booked_at
    delivery.last_error = None
    apply_price(delivery, booking.get("priceBreakdown"))
    delivery.last_payload = booking

    logger.info(
        "Lalamove order %s booked for %s (%s %s)",
        delivery.courier_order_id,
        order.order_number,
        delivery.quoted_currency or "",
        delivery.cost_total if delivery.cost_total is not None else "-",
    )

    # Committed here rather than left to the end of the request. A driver has
    # been engaged and the wallet debited — that happened outside our
    # transaction and cannot be rolled back with it. If anything later in the
    # request failed, we would lose the courier's order id while their order
    # kept existing, and the next dispatch would book a second driver for the
    # same cake. The status change this was triggered by is already applied on
    # the session, so committing both together is the state we want on disk.
    await db.commit()
    return delivery


# ── moving a third-party order onto this courier ──────────────────────────────
#
# A zone marked `third_party` has no integration by design: somebody we already
# use collects the box and tells us nothing. That is fine until it is not — the
# usual partner is full, or the order is late, or the address turns out to be
# fifteen minutes from the kitchen — and the shop wants this order, today, on a
# courier we can actually book.
#
# What makes that cheap is that nothing here is a second dispatch path. The
# three gates that keep a third-party order away from the courier
# (`courier_service.books_itself`, `batching_service.assign_or_dispatch`,
# `dispatch_order`) all read one column, so moving the order is a matter of
# changing `provider` and letting the machinery that already exists take it:
# webhook matching by `courier_order_id`, driver fill, POD, `_advance_order`,
# the emails.
#
# It is deliberately two calls rather than one. The admin is agreeing to a
# specific number, and a single call that quoted and booked in one breath would
# be asking them to approve a price they had not seen.


@dataclass(frozen=True)
class ReassignQuote:
    """A price an admin can say yes to, and the booking it would become."""

    estimate: Estimate
    #: The quotation's own stops, kept so the booking uses the ids this price
    #: was calculated for rather than re-deriving them from a second quotation.
    stops: list[dict[str, Any]]
    expires_at: datetime | None


async def quote_for_order(
    db: AsyncSession, order: Order
) -> tuple[ReassignQuote | None, str | None]:
    """
    What Lalamove would charge to carry this order, right now.

    Returns `(quote, error)` and never raises, matching `estimate_for_point`.

    Built from `resolve_pickup` and `build_drop` — the same two calls
    `dispatch_order` makes — rather than from `estimate_for_point`. That
    function exists to price a pin while a customer drags it around and caches
    for two minutes on a rounded coordinate, which is exactly right for a
    checkout and exactly wrong here: the number shown to an admin about to spend
    money has to be the number that gets spent, quoted for the address the
    parcel is actually going to.
    """
    if not is_enabled():
        return None, "Courier is not configured"

    drop, reason = build_drop(order)
    if drop is None:
        return None, reason

    pickup = await resolve_pickup(db, order.branch_id)
    if pickup is None:
        return None, "No pickup branch is configured"

    try:
        quotation = await provider.create_quotation(
            [pickup.as_stop(), drop.stop],
            special_requests=special_requests(),
            timeout=settings.LALAMOVE_TIMEOUT_SECONDS,
        )
    except LalamoveError as exc:
        return None, (
            "Address is outside the courier's service area"
            if exc.is_out_of_service_area
            else str(exc)
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Unexpected error quoting Lalamove for %s", order.order_number)
        return None, f"Courier quote failed: {exc}"

    estimate = parse_quotation(quotation)
    if estimate is None or not estimate.quotation_id:
        return None, "Courier returned no price"

    stops = quotation.get("stops") or []
    if len(stops) < 2:
        return None, "Courier quote came back without stops"

    return (
        ReassignQuote(
            estimate=estimate,
            stops=stops,
            expires_at=parse_time(quotation.get("expiresAt")),
        ),
        None,
    )


class QuotationExpired(RuntimeError):
    """The approved price is no longer available.

    Raised rather than silently re-quoting: the whole point of the two-step
    flow is that a human agreed to a number, and booking at a different one
    because the first had lapsed would spend money nobody approved. The caller
    re-quotes and asks again.
    """


async def assign_and_dispatch(
    db: AsyncSession,
    order: Order,
    *,
    quote: ReassignQuote,
) -> OrderDelivery:
    """
    Move this order onto Lalamove and book it, at the price that was approved.

    Takes the quote rather than re-quoting. `dispatch_order` quotes afresh for
    good reasons — it runs unattended, minutes or hours after the order was
    placed — but here a person is looking at a figure and pressing a button, and
    the two must be the same figure.

    On failure the row lands on **third party**, not on whatever it said before.
    A row reading `lalamove` with no booking is the one state that stalls a
    paid, boxed order: every dispatch path then believes a courier is coming and
    nothing manual applies.

    It used to restore the previous provider, which was right while the only
    caller moved orders *from* third party — the old value was third party, and
    putting it back was the same thing. It is not right for
    `fulfilment_reassignment.move`, which cancels the outgoing booking before
    getting here: restoring `noon_send` would leave the order claiming a courier
    whose task we had just called off. Third party is the honest floor in both
    cases — somebody we already use can carry it, and `last_error` says why they
    have to.
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None:
        raise ValueError("Order has no delivery record")

    was = delivery.provider
    reference = await courier_reference.assign(db, delivery)

    drop, reason = build_drop(order, reference)
    if drop is None:
        delivery.last_error = reason
        return delivery

    pickup = await resolve_pickup(db, order.branch_id)
    if pickup is None:
        delivery.last_error = "No pickup branch is configured"
        return delivery

    stops = quote.stops
    try:
        booking = await provider.place_order(
            quotation_id=quote.estimate.quotation_id or "",
            sender={
                "stopId": stops[0].get("stopId"),
                "name": pickup.name,
                "phone": pickup.phone,
            },
            recipients=[drop.recipient(stops[1].get("stopId"))],
            is_pod_enabled=True,
            metadata={
                "order_number": order.order_number,
                "order_id": str(order.id),
                "courier_reference": reference or "",
            },
        )
    except LalamoveError as exc:
        if exc.is_expired_quotation:
            # Not a failure of ours and not the order's fault. Nothing has been
            # written, so the caller can re-quote and ask again.
            raise QuotationExpired(str(exc)) from exc
        delivery.provider = FulfilmentProviderEnum.THIRD_PARTY.value
        delivery.last_error = str(exc)
        logger.warning(
            "Lalamove reassignment failed for %s: %s", order.order_number, exc
        )
        return delivery
    except Exception as exc:  # pragma: no cover — defensive
        delivery.provider = FulfilmentProviderEnum.THIRD_PARTY.value
        delivery.last_error = f"Courier dispatch failed: {exc}"
        logger.exception("Unexpected error reassigning %s", order.order_number)
        return delivery

    # Booked. Only now does the row change hands.
    #
    # `original_provider` is set once and never overwritten. It means "what the
    # map chose for this order", so on an order moved twice — third party to
    # Lalamove, back, and out again — the first value is the true one and each
    # later hop must leave it alone. `fulfilment_reassignment.move` has usually
    # set it already by the time we get here; this is the path for the caller
    # that has not.
    if delivery.original_provider is None:
        delivery.original_provider = was
    delivery.provider = FulfilmentProviderEnum.LALAMOVE.value
    # Third-party orders never reach `assign_or_dispatch`, so this has been null
    # for the life of the order and the timeline's "packed" stamp with it. The
    # box is demonstrably ready — a courier has just been booked to collect it.
    if delivery.dispatchable_at is None:
        delivery.dispatchable_at = datetime.now(timezone.utc)

    delivery.quoted_cost = quote.estimate.cost
    delivery.quoted_currency = quote.estimate.currency
    delivery.quoted_distance_m = quote.estimate.distance_m
    delivery.quotation_id = quote.estimate.quotation_id
    delivery.quoted_at = datetime.now(timezone.utc)

    delivery.courier_order_id = booking.get("orderId")
    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = (
        booking.get("status") or CourierStatusEnum.ASSIGNING_DRIVER.value
    )
    delivery.share_link = booking.get("shareLink")
    await driver_assignment.clear(db, delivery)
    await driver_assignment.record(
        db,
        delivery,
        driver_assignment.Driver.from_lalamove(None, driver_id=booking.get("driverId")),
    )
    delivery.stop_id = stops[1].get("stopId")
    delivery.booked_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.booked_at
    delivery.last_error = None
    apply_price(delivery, booking.get("priceBreakdown"))
    delivery.last_payload = booking

    logger.info(
        "Order %s moved from %s to Lalamove order %s (%s %s)",
        order.order_number,
        was,
        delivery.courier_order_id,
        delivery.quoted_currency or "",
        delivery.cost_total if delivery.cost_total is not None else "-",
    )

    # Committed here for the same reason `dispatch_order` commits: a driver has
    # been engaged and the wallet debited, and neither rolls back with our
    # transaction. Losing the courier id while their order keeps existing is how
    # a second driver gets sent to the same cake.
    await db.commit()
    return delivery


async def cancel_delivery(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Call off the courier when the order is cancelled.

    Free while the driver is still being assigned. Past that Lalamove may
    charge, and we accept that rather than send a driver to a cancelled order.
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None or not delivery.courier_order_id:
        return delivery
    if delivery.courier_status in FAILED_COURIER_STATUSES:
        return delivery
    if not is_enabled():
        delivery.last_error = "Courier is not configured; cancel this booking by hand"
        return delivery

    try:
        await provider.cancel_order(delivery.courier_order_id)
    except LalamoveError as exc:
        delivery.last_error = f"Could not cancel with the courier: {exc}"
        logger.warning("Lalamove cancel failed for %s: %s", order.order_number, exc)
        return delivery

    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = CourierStatusEnum.CANCELED.value
    delivery.cancelled_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.cancelled_at
    delivery.cancel_party = "MERCHANT"
    delivery.cancel_reason = "order_cancelled"
    delivery.last_error = None
    return delivery


# ── inbound ───────────────────────────────────────────────────────────────────

#: Courier status -> the order status it implies. Statuses absent from this map
#: leave the order alone: ASSIGNING_DRIVER and ON_GOING are both "packed and
#: waiting", and a failed booking is a problem for an admin, not a state the
#: customer's order should move into on its own.
_ORDER_STATUS_FOR: dict[str, OrderStatusEnum] = {
    CourierStatusEnum.PICKED_UP.value: OrderStatusEnum.OUT_FOR_DELIVERY,
    CourierStatusEnum.COMPLETED.value: OrderStatusEnum.DELIVERED,
}


def courier_order_id_of(payload: dict[str, Any]) -> str | None:
    """The courier's own order id, wherever this event type happens to put it."""
    data = payload.get("data") or {}
    order = data.get("order") or {}
    value = order.get("orderId") or order.get("id")
    return str(value) if value else None


def replaced_order_id_of(payload: dict[str, Any]) -> str | None:
    """The booking an `ORDER_REPLACED` event supersedes, if this is one.

    Lalamove adjusts a matched order — a waiting fee, a route change — by
    cancelling it and cloning it under a new id, then telling us with
    `ORDER_REPLACED` and a `prevOrderId`. So for this one event type the id that
    finds our row is not the one in `data.order`.
    """
    if payload.get("eventType") != "ORDER_REPLACED":
        return None
    value = (payload.get("data") or {}).get("prevOrderId")
    return str(value) if value else None


def delivery_key_of(payload: dict[str, Any]) -> str | None:
    """Which courier id identifies the delivery row this event is about.

    The same as `courier_order_id_of` for every event except a replacement,
    where our row still holds the id being superseded.
    """
    return replaced_order_id_of(payload) or courier_order_id_of(payload)


async def handle_webhook(db: AsyncSession, raw_body: bytes) -> dict[str, Any]:
    """
    Verify, deduplicate and apply one push from Lalamove.

    They retry an unacknowledged event ten times over a day, so the same status
    change arrives more than once as a matter of course. Dedup is an insert on
    a unique index rather than a read-then-write, because two retries can land
    on two workers at the same instant.
    """
    payload = provider.verify_webhook(raw_body)
    event_id = payload.get("eventId")
    event_type = str(payload.get("eventType") or "UNKNOWN")
    if not event_id:
        raise LalamoveError("Webhook has no eventId")

    # The id that finds our row, which is not always the one in `data.order` —
    # a replacement names the booking it supersedes instead. Looking this up by
    # `data.order.orderId` matched nothing, so the clone that was actually
    # carrying the cake went unrecognised while the cancellation it arrived with
    # marked the order as needing a human.
    lookup_id = delivery_key_of(payload)
    deliveries: list[OrderDelivery] = []
    if lookup_id:
        deliveries = list(
            (
                await db.execute(
                    select(OrderDelivery)
                    .where(OrderDelivery.courier_order_id == lookup_id)
                    .options(selectinload(OrderDelivery.order))
                    .order_by(OrderDelivery.stop_sequence)
                )
            )
            .scalars()
            .all()
        )

    inserted = await db.execute(
        pg_insert(WebhookEvent)
        .values(
            provider="lalamove",
            event_id=str(event_id),
            event_type=event_type[:100],
            # A batched run covers several orders, so no single number belongs
            # in this column. The batch is the thing to look up by then, and it
            # is findable by the same courier id.
            order_number=(
                deliveries[0].order.order_number
                if len(deliveries) == 1 and deliveries[0].order
                else None
            ),
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    if inserted.rowcount == 0:
        logger.info("Duplicate Lalamove webhook skipped: %s", event_id)
        return {"received": True, "duplicate": True}

    if not deliveries:
        # A booking we have no record of — a manual order placed in the partner
        # portal, or a wallet event that names no order. Acknowledged so it is
        # not retried for a day, and left alone.
        return {"received": True, "event_type": event_type, "matched": False}

    # One courier order can be carrying fifteen of ours. Every one of them gets
    # the driver and the status; only proof of delivery is per-customer.
    for delivery in deliveries:
        await apply_webhook(db, payload, delivery=delivery)
    if lookup_id:
        await _apply_to_batch(db, payload, lookup_id)

    return {
        "received": True,
        "event_type": event_type,
        "matched": True,
        "deliveries": len(deliveries),
    }


async def _apply_to_batch(
    db: AsyncSession, payload: dict[str, Any], courier_order_id: str
) -> None:
    """Keep the run's own row in step with the orders riding on it."""
    batch = (
        (
            await db.execute(
                select(DeliveryBatch).where(
                    DeliveryBatch.courier_order_id == courier_order_id
                )
            )
        )
        .scalars()
        .first()
    )
    if batch is None:
        return

    data = payload.get("data") or {}
    courier_order = data.get("order") or {}
    driver = data.get("driver") or {}

    if payload.get("eventType") == "ORDER_REPLACED":
        # The run itself was cloned. Repoint it for the same reason the delivery
        # rows are repointed — otherwise every subsequent event for this run
        # arrives under an id the batch does not recognise.
        new_id = courier_order_id_of(payload)
        if new_id and new_id != batch.courier_order_id:
            batch.courier_order_id = new_id
            batch.last_payload = payload
        return

    if status := courier_order.get("status"):
        batch.courier_status = status
    if share_link := courier_order.get("shareLink"):
        batch.share_link = share_link
    if driver_id := (driver.get("driverId") or courier_order.get("driverId")):
        batch.driver_id = str(driver_id)
    if driver:
        batch.driver_name = driver.get("name") or batch.driver_name
        batch.driver_phone = driver.get("phone") or batch.driver_phone
        batch.driver_plate = driver.get("plateNumber") or batch.driver_plate
    if breakdown := courier_order.get("priceBreakdown"):
        total = decimal_or_none(breakdown.get("total"))
        if total is not None:
            batch.cost_total = total
        batch.price_breakdown = breakdown
    batch.last_payload = payload


def _apply_replacement(
    delivery: OrderDelivery, payload: dict[str, Any]
) -> OrderDelivery:
    """
    Follow a booking Lalamove cancelled and cloned under a new id.

    They do this to adjust an order that has already been matched — a waiting
    fee, an edited stop — and it arrives as three events: `CANCELED`, then
    `ASSIGNING_DRIVER`, then this. The first two are about the old booking and
    are indistinguishable from a real cancellation, so by the time this lands
    the row is already carrying `cancelled_at`, a `cancel_reason` and a
    `last_error` saying nobody is coming.

    All three are wrong about a parcel that is on its way, and the consequence
    was concrete: `needs_attention` goes true, the order appears on the admin's
    re-dispatch list, and re-dispatching it books a **second driver for the same
    cake** while the first is still carrying it.

    So this repoints the row and clears the phantom. The old id is archived
    rather than dropped — it is what the earlier webhooks and any support
    conversation quote.
    """
    new_id = courier_order_id_of(payload)
    if not new_id or new_id == delivery.courier_order_id:
        return delivery

    if delivery.courier_order_id:
        delivery.previous_courier_order_ids = [
            *(delivery.previous_courier_order_ids or []),
            delivery.courier_order_id,
        ]
    delivery.courier_order_id = new_id

    # The cancellation was bookkeeping, not an outcome. Cleared so the order
    # stops reading as abandoned — but `courier_status` is left as the
    # replacement's own events will set it, rather than guessed at here.
    delivery.cancelled_at = None
    delivery.cancel_party = None
    delivery.cancel_reason = None
    delivery.last_error = None
    if delivery.courier_status in FAILED_COURIER_STATUSES:
        delivery.courier_status = CourierStatusEnum.ASSIGNING_DRIVER.value

    if share_link := (payload.get("data") or {}).get("order", {}).get("shareLink"):
        delivery.share_link = share_link
    delivery.last_payload = payload

    logger.info(
        "Lalamove replaced booking %s with %s for delivery %s",
        delivery.previous_courier_order_ids[-1]
        if delivery.previous_courier_order_ids
        else "?",
        new_id,
        delivery.id,
    )
    return delivery


async def apply_webhook(
    db: AsyncSession,
    payload: dict[str, Any],
    delivery: OrderDelivery | None = None,
) -> OrderDelivery | None:
    """
    Fold one webhook into the delivery record, and move the order if it should.

    Lalamove does not promise chronological delivery of these, so an update
    stamped earlier than the one already applied is dropped rather than allowed
    to walk a completed order back to "assigning driver".
    """
    data = payload.get("data") or {}
    courier_order = data.get("order") or {}
    courier_order_id = courier_order_id_of(payload)
    lookup_id = delivery_key_of(payload)

    if delivery is None:
        if not lookup_id:
            return None
        delivery = (
            (
                await db.execute(
                    select(OrderDelivery).where(
                        OrderDelivery.courier_order_id == lookup_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if delivery is None:
            logger.info("Lalamove webhook for unknown order %s", lookup_id)
            return None

    if payload.get("eventType") == "ORDER_REPLACED":
        return _apply_replacement(delivery, payload)

    updated_at = webhook_time(payload)
    if (
        updated_at is not None
        and delivery.status_updated_at is not None
        and updated_at < delivery.status_updated_at
    ):
        logger.info(
            "Ignoring out-of-order Lalamove webhook for %s (%s < %s)",
            courier_order_id,
            updated_at,
            delivery.status_updated_at,
        )
        return delivery

    event_type = payload.get("eventType")
    delivery.last_payload = payload

    # ── who is carrying it ────────────────────────────────────────────────────
    #
    # Both shapes are folded into one decision. `DRIVER_ASSIGNED` describes the
    # whole person; the status change that production actually receives carries
    # a bare `driverId` and nothing else. Either can be the *first* driver or a
    # replacement for one, and `driver_assignment.record` is what tells them
    # apart — this used to ask `not had_driver`, which is only ever true once
    # and so made a mid-booking swap invisible.
    reported = driver_assignment.Driver.from_lalamove(
        data.get("driver") if event_type == "DRIVER_ASSIGNED" else None,
        driver_id=courier_order.get("driverId"),
    )
    change = await driver_assignment.record(db, delivery, reported, at=updated_at)

    if event_type == "DRIVER_ASSIGNED":
        location = data.get("location") or {}
        await driver_assignment.record_position(
            db,
            delivery,
            latitude=decimal_or_none(location.get("lat")),
            longitude=decimal_or_none(location.get("lng")),
            at=updated_at,
        )

    if event_type == "POD_STATUS_CHANGED":
        pod = _pod_for(delivery, courier_order.get("stops") or [])
        if pod:
            delivery.pod_status = pod.get("status") or delivery.pod_status
            delivery.pod_image_url = pod.get("image") or delivery.pod_image_url

    apply_price(delivery, courier_order.get("priceBreakdown"))
    if courier_order.get("shareLink"):
        delivery.share_link = courier_order["shareLink"]

    # A driver we had not been told about, or a different one from last time.
    # Handled here rather than only under `DRIVER_ASSIGNED`, because that event
    # has never once arrived: across every webhook production has received the
    # types were ORDER_CREATED, ORDER_STATUS_CHANGED, POD_STATUS_CHANGED and
    # WALLET_BALANCE_CHANGED, and the driver id came in on the status change.
    # Hanging the announcement off an event we do not get meant the counter was
    # never told a rider was coming.
    if change.is_new_driver:
        # The status push carries an id and no name, so the shop has nothing to
        # say or ring until this fetches one.
        await fill_driver_details(db, delivery, at=updated_at)
        # Routed here rather than left to the sweep's next tick. The register
        # prints a driver slip off this within seconds, and a slip is paper —
        # one that goes out with the ETA missing cannot be corrected.
        await driver_routing.route_now(db, delivery)
        await announce_driver(db, delivery)

    status = courier_order.get("status")
    if status and status != delivery.courier_status:
        delivery.courier_previous_status = (
            courier_order.get("previousStatus") or delivery.courier_status
        )
        delivery.courier_status = status
        # `picked_up_at` and `delivered_at` used to be written here as well.
        # They were the same two moments `order_status_events` now records as
        # `out_for_delivery` and `delivered` — the same fact in two tables, free
        # to disagree, and they did not have to disagree by much to matter: one
        # of them fed the customer's timeline and the other fed the admin's.
        # `_advance_order` carries the courier's own stamp into the history, so
        # there is one copy and it is the courier's.
        if status in FAILED_COURIER_STATUSES:
            delivery.cancelled_at = updated_at or datetime.now(timezone.utc)
            delivery.cancel_party = courier_order.get("cancelParty")
            delivery.cancel_reason = courier_order.get("cancelReason")
            # Nobody is coming. The order stays where it is so an admin can
            # re-dispatch rather than the customer being told it is cancelled.
            delivery.last_error = (
                f"Courier {status.lower()} the booking — re-dispatch required"
            )

    if updated_at is not None:
        delivery.status_updated_at = updated_at
    elif status:
        delivery.status_updated_at = datetime.now(timezone.utc)

    await _advance_order(db, delivery, at=updated_at)
    return delivery


async def fill_driver_details(
    db: AsyncSession,
    delivery: OrderDelivery,
    *,
    at: datetime | None = None,
) -> None:
    """
    Put a name, a number and a position to the driver id.

    The status webhook carries only `driverId`, so without this every Lalamove
    delivery kept an empty `driver_name` and the shop had no way to reach the
    person holding the cake — the two production orders both ended that way.

    Public rather than private because `driver_tracking` calls it on every tick
    of the sweep: this endpoint is the only place Lalamove will say where a
    driver is, and a position asked for once at assignment is a position that is
    twenty minutes old by pickup.

    Best-effort: the webhook must still answer 200, and a delivery with an
    anonymous driver is worse than one with a named driver but far better than
    a status update we dropped on the floor.
    """
    if not delivery.courier_order_id or not delivery.driver_id:
        return
    if not is_enabled():
        return
    try:
        driver = await provider.get_driver(
            str(delivery.courier_order_id), str(delivery.driver_id)
        )
    except Exception:  # pragma: no cover — network, and never worth failing on
        logger.warning(
            "Could not read driver %s for order %s",
            delivery.driver_id,
            delivery.courier_order_id,
        )
        return

    detail = driver.get("data") if isinstance(driver.get("data"), dict) else driver
    # Through `record` rather than written straight onto the row: this endpoint
    # is asked about a driver id we already hold, so it can only ever confirm or
    # enrich that person, and `record` is where "the same person, better
    # described" is distinguished from a swap.
    await driver_assignment.record(
        db,
        delivery,
        driver_assignment.Driver.from_lalamove(detail, driver_id=delivery.driver_id),
        at=at,
    )
    coords = detail.get("coordinates") or {}
    await driver_assignment.record_position(
        db,
        delivery,
        latitude=decimal_or_none(coords.get("lat")),
        longitude=decimal_or_none(coords.get("lng")),
        # Their `coordinates.updatedAt` where they send one, because a position
        # is only as good as the moment it was true and this one may already be
        # a minute old by the time we ask.
        at=parse_time(coords.get("updatedAt")) or at or datetime.now(timezone.utc),
    )


async def announce_driver(db: AsyncSession, delivery: OrderDelivery) -> None:
    """
    Tell the counter somebody is coming for it — or that somebody else is now.

    Best-effort, and deliberately swallowed: this runs inside a webhook that
    must answer 200, and a notification that fails is a quieter shop rather
    than a lost status update.
    """
    from app.services import push_service

    order = delivery.order or (
        (await db.execute(select(Order).where(Order.id == delivery.order_id)))
        .scalars()
        .first()
    )
    if order is None or not getattr(order, "branch_id", None):
        return
    try:
        await push_service.notify_rider_assigned(
            db,
            order,
            rider_name=delivery.driver_name,
            rider_phone=delivery.driver_phone,
            assignment=delivery.driver_assignment_count or 1,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("Could not announce the driver for %s", order.order_number)


def _pod_for(delivery: OrderDelivery, stops: list[dict[str, Any]]) -> dict | None:
    """
    The proof belonging to *this* customer, out of a route with many.

    On a shared run every stop reports its own POD, and attaching the last one
    to everybody would put a photo of somebody else's doorway on the wrong
    order. Matched by stop id where Lalamove sends one and by coordinates where
    it does not; on a solo run, where there is only ever one drop, the first
    proof on the route is unambiguous.
    """
    with_pod = [stop for stop in stops if stop.get("POD")]
    if not with_pod:
        return None

    if delivery.stop_id:
        for stop in with_pod:
            if stop.get("stopId") == delivery.stop_id:
                return stop["POD"]

    address = (delivery.order.shipping_address_snapshot or {}) if delivery.order else {}
    latitude, longitude = _coordinates(address)
    if latitude is not None and longitude is not None:
        target = (f"{latitude:.4f}", f"{longitude:.4f}")
        for stop in with_pod:
            coords = stop.get("coordinates") or {}
            try:
                here = (
                    f"{float(coords.get('lat')):.4f}",
                    f"{float(coords.get('lng')):.4f}",
                )
            except (TypeError, ValueError):
                continue
            if here == target:
                return stop["POD"]

    # A solo booking has exactly one drop, so there is nothing to confuse it
    # with. On a shared run, refusing to guess is the right answer.
    if delivery.batch_id is None:
        return with_pod[0]["POD"]

    logger.info("POD on a shared run did not match a stop for delivery %s", delivery.id)
    return None


async def _advance_order(
    db: AsyncSession, delivery: OrderDelivery, *, at: datetime | None = None
) -> None:
    """Move the order to match the courier, stamped with the courier's clock.

    `at` is the moment their push describes — `webhook_time`, taken from the
    payload's epoch rather than its formatted string, because their string is
    Gulf local time carrying a `Z` and parsing it put every delivery four hours
    in the future. Null when the push carried nothing usable, and the history
    then falls back to now.
    """
    target = _ORDER_STATUS_FOR.get(delivery.courier_status or "")
    if target is None:
        return

    # The lines are loaded because this order ends up at the mailer, and
    # `OrderResponse` reads `order.items`. Selected bare that is a
    # `MissingGreenlet` rather than a query — the failure mode that silently
    # swallowed four customer emails on 2026-08-05.
    order = (
        (
            await db.execute(
                select(Order)
                .options(selectinload(Order.items))
                .where(Order.id == delivery.order_id)
            )
        )
        .scalars()
        .first()
    )
    if order is None:
        return

    # One rulebook, not a private copy of it. The guards that used to live here
    # — settled orders stay settled, `out_for_delivery` only from a state that
    # could plausibly hand a box to a rider — are the map's own rules, enforced
    # where every other writer gets them. Going through `transition` also means
    # the consequences of a status are the same whoever reports it, whichever
    # statuses this courier's map grows next.
    #
    # The courier's own word for what happened goes in the note, so the history
    # records `PICKED_UP` next to `out_for_delivery` rather than only our
    # translation of it.
    with acting_as(
        StatusSourceEnum.COURIER.value,
        actor_label=delivery.provider,
        note=delivery.courier_status,
        at=at,
    ):
        moved = await order_lifecycle.transition(db, order, target, on_invalid="skip")
    if not moved:
        return
    # The customer hears about it from here, not from the admin screen. These
    # two transitions only ever happen because a courier said so, so this is the
    # only place that knows they happened — and "out for delivery" is the email
    # that carries the live tracking link, which is worth nothing an hour late.
    await email_service.notify_order(db, order)


# ── helpers ───────────────────────────────────────────────────────────────────


def special_requests() -> list[str]:
    raw = settings.LALAMOVE_SPECIAL_REQUESTS or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_quotation(quotation: dict[str, Any]) -> Estimate | None:
    breakdown = quotation.get("priceBreakdown") or {}
    total = decimal_or_none(breakdown.get("total"))
    if total is None:
        return None
    distance = quotation.get("distance") or {}
    value = distance.get("value")
    try:
        distance_m = int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        distance_m = None
    return Estimate(
        cost=total,
        currency=breakdown.get("currency") or "AED",
        distance_m=distance_m,
        quotation_id=quotation.get("quotationId"),
    )


def apply_price(delivery: OrderDelivery, breakdown: Any) -> None:
    if not isinstance(breakdown, dict):
        return
    total = decimal_or_none(breakdown.get("total"))
    if total is not None:
        delivery.cost_total = total
    delivery.price_breakdown = breakdown


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    """A naive stamp is read as UTC; anything unparseable is None."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def webhook_time(payload: dict[str, Any]) -> datetime | None:
    """
    When a Lalamove webhook says its event happened.

    Read off the top-level epoch `timestamp`, **not** `data.updatedAt`, because
    `updatedAt` is wrong in two separate ways that happen to cancel out into a
    plausible-looking value:

    1. It is Gulf local time carrying a `Z`. Both production orders prove it —
       task 3554059933802783529 arrived stamped `2026-08-03T21:38.00Z` with
       `timestamp` 1785778684, which is 17:38:04 UTC, and our own clock agreed
       with the epoch to the second. Trusting the string put `delivered_at`
       four hours in the future on every order.
    2. Its format is `HH:MM.ss`, which is not a time. Python 3.12 happens to
       accept it, 3.14 raises — so a runtime upgrade silently changes what gets
       recorded, and the out-of-order guard that depends on it goes dead
       without a test failing.

    The epoch has neither problem: it is unambiguous, it needs no parsing, and
    it agreed with the received-at time on every webhook we have.
    """
    epoch = payload.get("timestamp")
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _coordinates(address: dict[str, Any]) -> tuple[float | None, float | None]:
    try:
        return float(address["latitude"]), float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _recipient_name(address: dict[str, Any]) -> str:
    return address_format.recipient_name(address) or ""


def _drop_address(address: dict[str, Any]) -> str:
    """
    What the driver reads.

    `address_format.one_line`, so the driver's stop, the register's ticket and
    the customer's confirmation email all say the same thing. It used to drop
    `address_line_2`, which is where a checkout puts a building name.
    """
    return address_format.one_line(address) or ""


def _remarks(
    order: Order, address: dict[str, Any], reference: str | None = None
) -> str:
    """
    What the driver reads, led by the number they will be asked to quote.

    The seven-digit reference goes first for the same reason noon Send asked for
    one: `MM-20260805-007` is a poor thing to read down a phone. Our own number
    follows it, because the person on the other end of that phone is looking at
    an admin screen that lists orders by it.
    """
    bits = [f"Order {reference or order.order_number}"]
    if reference:
        bits.append(f"Ref {order.order_number}")
    unit = str(address.get("unit_number") or "").strip()
    if unit:
        bits.append(f"Unit {unit}")
    if order.notes:
        bits.append(str(order.notes).strip())
    return " · ".join(bits)[:200]


# `normalise_phone` used to be defined here and now lives in `core.phone`. It is
# imported at the top of this module and re-exported through `__all__`, so this
# module's callers and its tests still name it where they always have — one copy
# of the rules, because two copies are two answers to "is this the same number",
# which is exactly the disagreement that lets one coupon be claimed twice.
