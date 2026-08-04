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
from app.models.branch import Branch
from app.models.cart import Cart
from app.models.delivery_batch import DeliveryBatch
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import (
    FAILED_COURIER_STATUSES,
    CourierStatusEnum,
    OrderDelivery,
)
from app.models.webhook_event import WebhookEvent
from app.services.delivery_zone_service import Zone
from app.services.providers.lalamove_provider import LalamoveError, provider

logger = logging.getLogger(__name__)

__all__ = [
    "Estimate",
    "PickupPoint",
    "Drop",
    "apply_price",
    "apply_webhook",
    "build_drop",
    "cancel_delivery",
    "clear_caches",
    "courier_order_id_of",
    "decimal_or_none",
    "dispatch_order",
    "estimate_for_point",
    "get_delivery",
    "handle_webhook",
    "is_enabled",
    "normalise_phone",
    "parse_quotation",
    "parse_time",
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
    #: What noon Send calls this branch. Empty means it is not registered with
    #: them, so it can be a Lalamove pickup but not a noon Send one. Lalamove
    #: needs no equivalent — it takes coordinates and an address.
    noon_send_outlet_code: str = ""

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

    Without one, it falls back to what every order used before: configured by
    reference when there is more than one candidate, otherwise the first active
    branch that takes online orders and has a pin — because a branch without
    coordinates cannot be a pickup stop no matter how it is flagged.
    """
    stmt = select(Branch).where(
        Branch.is_active.is_(True),
        Branch.deleted_at.is_(None),
        Branch.latitude.isnot(None),
        Branch.longitude.isnot(None),
    )
    if branch_id is not None:
        stmt = stmt.where(Branch.id == branch_id)
    elif settings.LALAMOVE_PICKUP_BRANCH_REF:
        stmt = stmt.where(Branch.reference == settings.LALAMOVE_PICKUP_BRANCH_REF)
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

    phone = normalise_phone(settings.LALAMOVE_SENDER_PHONE or branch.phone or "")
    if not phone:
        logger.warning(
            "Branch %s has no phone number; Lalamove needs one for the sender",
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
        # The setting is the fallback, not the source: a branch that names its
        # own outlet wins, so a second kitchen is a row in the admin rather than
        # a deploy.
        noon_send_outlet_code=(
            branch.noon_send_outlet_code or settings.NOON_SEND_OUTLET_CODE or ""
        ),
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

    Returns `(estimate, error)` and never raises: this runs inside the pricing
    call the checkout page makes on every keystroke-ish change, and a courier
    outage must not be able to stop someone buying a cake.
    """
    if not is_enabled():
        return None, None

    # ~11 m of precision. Finer than a building, coarser than the jitter a
    # dragged pin produces. Keyed on the origin too: the same doorstep costs a
    # different amount from a different kitchen, and a shared key would serve
    # one branch's price for another's run.
    key = (branch_id, round(latitude, 4), round(longitude, 4))
    cached = _quote_cache.get(key)
    if cached and time.monotonic() - cached[0] < settings.LALAMOVE_QUOTE_CACHE_SECONDS:
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
            # Half the checkout budget: a quote we will not show is not worth
            # making anyone wait for.
            timeout=min(settings.LALAMOVE_TIMEOUT_SECONDS, 4.0),
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
        zone.fulfilment_provider if zone else FulfilmentProviderEnum.THIRD_PARTY.value
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
    """
    delivery = OrderDelivery(
        order_id=order.id,
        provider=(
            zone.fulfilment_provider
            if zone
            else FulfilmentProviderEnum.THIRD_PARTY.value
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


def build_drop(order: Order) -> tuple[Drop | None, str | None]:
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
            remarks=_remarks(order, address),
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

    drop, reason = build_drop(order)
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
    delivery.driver_id = booking.get("driverId") or None
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

    courier_order_id = courier_order_id_of(payload)
    deliveries: list[OrderDelivery] = []
    if courier_order_id:
        deliveries = list(
            (
                await db.execute(
                    select(OrderDelivery)
                    .where(OrderDelivery.courier_order_id == courier_order_id)
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
    if courier_order_id:
        await _apply_to_batch(db, payload, courier_order_id)

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

    if delivery is None:
        if not courier_order_id:
            return None
        delivery = (
            (
                await db.execute(
                    select(OrderDelivery).where(
                        OrderDelivery.courier_order_id == courier_order_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if delivery is None:
            logger.info("Lalamove webhook for unknown order %s", courier_order_id)
            return None

    updated_at = parse_time(data.get("updatedAt"))
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

    if event_type == "DRIVER_ASSIGNED":
        driver = data.get("driver") or {}
        delivery.driver_id = driver.get("driverId") or delivery.driver_id
        delivery.driver_name = driver.get("name") or delivery.driver_name
        delivery.driver_phone = driver.get("phone") or delivery.driver_phone
        delivery.driver_plate = driver.get("plateNumber") or delivery.driver_plate
        location = data.get("location") or {}
        delivery.driver_latitude = decimal_or_none(location.get("lat"))
        delivery.driver_longitude = decimal_or_none(location.get("lng"))

    if event_type == "POD_STATUS_CHANGED":
        pod = _pod_for(delivery, courier_order.get("stops") or [])
        if pod:
            delivery.pod_status = pod.get("status") or delivery.pod_status
            delivery.pod_image_url = pod.get("image") or delivery.pod_image_url

    apply_price(delivery, courier_order.get("priceBreakdown"))
    if courier_order.get("shareLink"):
        delivery.share_link = courier_order["shareLink"]
    if courier_order.get("driverId"):
        delivery.driver_id = str(courier_order["driverId"])

    status = courier_order.get("status")
    if status and status != delivery.courier_status:
        delivery.courier_previous_status = (
            courier_order.get("previousStatus") or delivery.courier_status
        )
        delivery.courier_status = status
        if status == CourierStatusEnum.PICKED_UP.value:
            delivery.picked_up_at = updated_at or datetime.now(timezone.utc)
        elif status == CourierStatusEnum.COMPLETED.value:
            delivery.delivered_at = updated_at or datetime.now(timezone.utc)
        elif status in FAILED_COURIER_STATUSES:
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

    await _advance_order(db, delivery)
    return delivery


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


async def _advance_order(db: AsyncSession, delivery: OrderDelivery) -> None:
    target = _ORDER_STATUS_FOR.get(delivery.courier_status or "")
    if target is None:
        return

    order = (
        (await db.execute(select(Order).where(Order.id == delivery.order_id)))
        .scalars()
        .first()
    )
    if order is None:
        return
    # Refunded, disputed and cancelled orders are settled. A late courier
    # update must not resurrect one.
    if order.status in {
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
        target,
    }:
        return
    if target == OrderStatusEnum.OUT_FOR_DELIVERY and order.status not in {
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.PACKED,
    }:
        return

    order.status = target


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
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _coordinates(address: dict[str, Any]) -> tuple[float | None, float | None]:
    try:
        return float(address["latitude"]), float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None


def _recipient_name(address: dict[str, Any]) -> str:
    parts = [address.get("first_name"), address.get("last_name")]
    return " ".join(str(p).strip() for p in parts if p).strip()


def _drop_address(address: dict[str, Any]) -> str:
    """
    What the driver reads.

    The unit number leads, because a formatted Google address gets someone to
    the building and the flat number is the only part that finishes the job.
    """
    line = str(address.get("address_line_1") or "").strip()
    unit = str(address.get("unit_number") or "").strip()
    return f"{unit}, {line}" if unit and line else (line or unit)


def _remarks(order: Order, address: dict[str, Any]) -> str:
    bits = [f"Order {order.order_number}"]
    unit = str(address.get("unit_number") or "").strip()
    if unit:
        bits.append(f"Unit {unit}")
    if order.notes:
        bits.append(str(order.notes).strip())
    return " · ".join(bits)[:200]


def normalise_phone(raw: str) -> str:
    """
    E.164, or nothing.

    The storefront already sends E.164; this is for numbers typed at the
    counter or imported from elsewhere. A number we cannot make sense of is
    returned empty rather than guessed at, because a wrong number on a booking
    strands a driver outside a building.
    """
    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits if len(digits) >= 8 else ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("971"):
        return f"+{digits}"
    if digits.startswith("0"):
        digits = digits[1:]
    if 8 <= len(digits) <= 10:
        return f"+971{digits}"
    return f"+{digits}" if len(digits) >= 10 else ""
