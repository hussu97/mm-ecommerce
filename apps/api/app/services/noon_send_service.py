"""
The bridge between an order and a noon Send task.

Same three rules as the Lalamove bridge — a courier failure is never a customer
failure, the price the customer pays comes from the zone map and not from here,
and a third-party zone behaves exactly as it did before — plus one of its own.

**Nobody tells us what a run cost.** noon Send has no quotation endpoint, the
create-task response carries no price, and neither do the task details. The only
cost figure that will ever exist for one of their tasks is the one this module
computes from their published rate card:

    AED 12            for the first 10 km
      + 1.00 per km   for  10-20 km
      + 1.50 per km   beyond 20 km

Distance is straight line from the kitchen times `NOON_SEND_DETOUR_FACTOR`,
which is fitted (1.49x) against the sixteen Sharjah areas the live Lalamove rate
card was measured over. It is an estimate and it is labelled as one everywhere it
surfaces; treating it as a billed figure would be inventing precision we do not
have.

For the shape of the money that estimate is compared against: at 15 AED charged,
noon Send costs 12 across most of Sharjah Central where Lalamove costs 19-26.
That gap is the whole reason this file exists.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderPayment
from app.models.order_delivery import (
    NOON_SEND_FAILED_STATUSES,
    NoonSendStatusEnum,
    OrderDelivery,
)
from app.models.webhook_event import WebhookEvent
from app.services.lalamove_service import (
    Estimate,
    decimal_or_none,
    get_delivery,
    normalise_phone,
    parse_time,
    resolve_pickup,
)
from app.services.providers.noon_send_provider import (
    NoonSendError,
    fils,
    provider,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER",
    "apply_tracking",
    "apply_webhook",
    "cancel_delivery",
    "dispatch_order",
    "estimate_for_point",
    "handle_tracking_webhook",
    "handle_webhook",
    "is_enabled",
    "may_serve",
    "rate_card_cost",
    "road_distance_km",
]

PROVIDER = FulfilmentProviderEnum.NOON_SEND.value

#: Kilometres per degree of latitude. Good to a fraction of a percent over the
#: fifteen kilometres this is ever asked about.
_KM_PER_DEG_LAT = 111.32


def is_enabled() -> bool:
    """Whether we can create a task at all.

    Without a key or a pickup point a `noon_send` zone still prices and sells
    normally — it just dispatches through Lalamove instead.
    """
    return provider.is_configured


# ── the rate card ─────────────────────────────────────────────────────────────


def road_distance_km(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """
    Roughly how far a rider actually drives between two points.

    Straight line on a local equirectangular approximation, scaled by the
    measured detour factor. Not accurate enough to bill from, which is why
    nothing bills from it — accurate enough to say what a zone costs on average,
    which is what it is for.
    """
    mean_lat = math.radians((lat_a + lat_b) / 2)
    dy = (lat_b - lat_a) * _KM_PER_DEG_LAT
    dx = (lng_b - lng_a) * _KM_PER_DEG_LAT * math.cos(mean_lat)
    return math.hypot(dx, dy) * settings.NOON_SEND_DETOUR_FACTOR


def rate_card_cost(distance_km: float) -> Decimal:
    """
    What noon Send charges for a run of this length.

    The bands are marginal, not selective: the 12 AED covers the first ten
    kilometres and each band prices only the distance inside it. Read the other
    way the card would price eleven kilometres below ten, which no courier means.
    """
    if distance_km <= 10:
        total = 12.0
    elif distance_km <= 20:
        total = 12.0 + (distance_km - 10) * 1.00
    else:
        total = 22.0 + (distance_km - 20) * 1.50
    return Decimal(f"{total:.2f}")


async def estimate_for_point(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    address: str | None = None,
) -> tuple[Estimate | None, str | None]:
    """
    What a noon Send run to this point would cost us, or why we cannot say.

    Signature-compatible with `lalamove_service.estimate_for_point` so the
    checkout path does not have to know which courier a zone uses. Makes no
    network call — there is nothing to call — so unlike the Lalamove version it
    costs nothing and cannot time out.
    """
    pickup = await resolve_pickup(db)
    if pickup is None:
        return None, "No pickup branch is configured"

    distance = road_distance_km(pickup.latitude, pickup.longitude, latitude, longitude)
    distance_m = int(distance * 1000)
    if distance_m > settings.NOON_SEND_MAX_DISTANCE_M:
        return None, (
            f"{distance:.1f} km is past noon Send's "
            f"{settings.NOON_SEND_MAX_DISTANCE_M / 1000:.0f} km limit"
        )

    return (
        Estimate(
            cost=rate_card_cost(distance),
            currency="AED",
            distance_m=distance_m,
            # No quotation exists to reference. Left empty rather than filled
            # with something invented, so a row with an id came from a courier
            # that actually issued one.
            quotation_id=None,
        ),
        None,
    )


async def may_serve(db: AsyncSession, order: Order) -> tuple[bool, str | None]:
    """
    Whether this order is inside noon Send's reach, decided before we call them.

    Their cap is a hard rejection at task creation, and a rejection on
    production costs a cancellation fee once a rider has been engaged. Checking
    here turns that into a free local decision.
    """
    address = order.shipping_address_snapshot or {}
    try:
        latitude = float(address["latitude"])
        longitude = float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return False, "Order has no delivery coordinates"

    pickup = await resolve_pickup(db)
    if pickup is None:
        return False, "No pickup branch is configured"
    if not pickup.noon_send_outlet_code:
        # Named, because the fix is a field in the admin rather than anything in
        # code, and "noon Send is not configured" would send someone looking in
        # the wrong place entirely.
        return False, (
            f"Branch {pickup.reference or pickup.name} has no noon Send outlet "
            "code — register it and set it on the branch"
        )

    distance = road_distance_km(pickup.latitude, pickup.longitude, latitude, longitude)
    limit_km = settings.NOON_SEND_MAX_DISTANCE_M / 1000
    if distance > limit_km:
        return False, (
            f"Drop-off is about {distance:.1f} km away, past noon Send's "
            f"{limit_km:.0f} km limit"
        )
    return True, None


# ── dispatch ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Task:
    """One order in the shape noon Send wants it."""

    order_reference: str
    drop_off_address: dict[str, Any]
    prepaid_value: int
    cod_value: int
    delivery_notes: str
    tags: list[str]


async def outstanding_balance(db: AsyncSession, order: Order) -> Decimal:
    """
    What is still owed on this order, asked for rather than read off the object.

    `Order.amount_paid` walks `order.payments`, which is a relationship. It is
    `lazy="selectin"` so it is already loaded whenever the order came out of a
    query — but `dispatch_order` is called from four places and only has to meet
    one order that did not, and a lazy load from inside async SQLAlchemy is not
    a wrong number, it is a `MissingGreenlet` and a failed dispatch. Summing in
    SQL is one query and cannot be surprised.
    """
    paid = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (OrderPayment.is_refund.is_(True), -OrderPayment.amount),
                            else_=OrderPayment.amount,
                        )
                    ),
                    0,
                )
            ).where(OrderPayment.order_id == order.id)
        )
    ).scalar() or Decimal("0")
    return Decimal(str(order.total or 0)) - Decimal(str(paid))


def build_task(order: Order, outstanding: Decimal) -> tuple[Task | None, str | None]:
    """
    Turn an order's shipping snapshot into a task, or say why it cannot be one.

    Mirrors `lalamove_service.build_drop`: the reason is written to the delivery
    row so an admin reads "no reachable phone number" rather than a 422 about a
    field they have never heard of. Reads nothing but plain columns, for the
    same reason — `outstanding` is passed in rather than derived here.
    """
    address = order.shipping_address_snapshot or {}
    try:
        latitude = float(address["latitude"])
        longitude = float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, "Order has no delivery coordinates"

    phone = normalise_phone(str(address.get("phone") or ""))
    if not phone:
        return None, "Order has no reachable phone number"

    line = str(address.get("address_line_1") or "").strip()
    unit = str(address.get("unit_number") or "").strip()
    # Unit first, for the same reason as Lalamove: a formatted Google address
    # gets a rider to the building and the flat number finishes the job.
    text = f"{unit}, {line}" if unit and line else (line or unit)
    if len(text) < 5:
        return None, "Order has no usable street address"

    name = " ".join(
        str(part).strip()
        for part in (address.get("first_name"), address.get("last_name"))
        if part
    ).strip()

    total = Decimal(str(order.total or 0))
    is_cod = (order.payment_method or "").lower() == "cod" and outstanding > 0

    notes = [f"Order {order.order_number}"]
    if unit:
        notes.append(f"Unit {unit}")
    if order.notes:
        notes.append(str(order.notes).strip())

    return (
        Task(
            order_reference=order.order_number,
            drop_off_address={
                "lat": round(latitude * 10_000_000),
                "lng": round(longitude * 10_000_000),
                "address": text[:10_000],
                "contact_name": (name or order.order_number)[:100],
                "contact_phone_number": phone,
                "country_code": "ae",
                "city": str(address.get("city") or "Sharjah")[:100],
            },
            prepaid_value=0 if is_cod else fils(total),
            cod_value=fils(outstanding) if is_cod else 0,
            delivery_notes=" · ".join(notes)[:250],
            # A cake is handed over, never left at a door — and a COD task may
            # not carry a leave-it tag at all.
            tags=[],
        ),
        None,
    )


async def dispatch_order(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Create one noon Send task for one order.

    Returns the delivery row with `courier_order_id` set on success, and with
    `last_error` set and no id on failure — the caller (`courier_service`)
    decides whether that failure is worth handing to Lalamove.

    Committed on success for the same reason the Lalamove path commits: a rider
    has been engaged outside our transaction and cannot be rolled back with it,
    so losing the task number while their task keeps existing would put two
    riders on one cake.
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None:
        return None

    pickup = await resolve_pickup(db)
    if pickup is None:
        delivery.last_error = "No pickup branch is configured"
        return delivery
    if not pickup.noon_send_outlet_code:
        delivery.last_error = (
            f"Branch {pickup.reference or pickup.name} has no noon Send outlet "
            "code — register it and set it on the branch"
        )
        return delivery

    task, reason = build_task(order, await outstanding_balance(db, order))
    if task is None:
        delivery.last_error = reason
        return delivery

    try:
        created = await provider.create_task(
            order_reference=task.order_reference,
            # The branch that actually resolved, not a global. When a second
            # kitchen starts delivering this is already the right value.
            outlet_code=pickup.noon_send_outlet_code,
            drop_off_address=task.drop_off_address,
            prepaid_value=task.prepaid_value,
            cod_value=task.cod_value,
            delivery_notes=task.delivery_notes,
            tags=task.tags,
            # The order number, so a retry after a timeout is the same request
            # to them rather than a second rider to us.
            idempotency_key=order.order_number,
        )
    except NoonSendError as exc:
        delivery.last_error = f"noon Send: {exc}"
        logger.warning("noon Send dispatch failed for %s: %s", order.order_number, exc)
        return delivery
    except Exception as exc:  # pragma: no cover — defensive
        delivery.last_error = f"noon Send dispatch failed: {exc}"
        logger.exception("Unexpected error dispatching %s", order.order_number)
        return delivery

    task_nr = created.get("mp_task_nr")
    if not task_nr:
        delivery.last_error = "noon Send accepted the task but returned no task number"
        return delivery

    if delivery.courier_order_id:
        delivery.previous_courier_order_ids = [
            *(delivery.previous_courier_order_ids or []),
            delivery.courier_order_id,
        ]

    estimate, _ = await estimate_for_point(
        db,
        float(order.shipping_address_snapshot["latitude"]),
        float(order.shipping_address_snapshot["longitude"]),
    )
    if estimate is not None:
        delivery.quoted_cost = estimate.cost
        delivery.quoted_currency = estimate.currency
        delivery.quoted_distance_m = estimate.distance_m
        delivery.quoted_at = datetime.now(timezone.utc)
        # There is no invoice to reconcile against, ever. The rate card is both
        # the estimate and the best statement of cost we will have.
        delivery.cost_total = estimate.cost
        delivery.price_breakdown = {
            "source": "noon_send_rate_card",
            "distance_km": round((estimate.distance_m or 0) / 1000, 2),
            "total": str(estimate.cost),
            "currency": "AED",
            "is_estimate": True,
        }

    delivery.provider = PROVIDER
    delivery.courier_order_id = str(task_nr)
    delivery.courier_previous_status = delivery.courier_status
    # `created["status"]` is an acknowledgement, not a state — it comes back as
    # the literal "successful". The task's real opening status, confirmed
    # against staging, is `pending_assignment`, and storing their ack instead
    # would put a word in `courier_status` that no status map has ever heard of.
    delivery.courier_status = NoonSendStatusEnum.PENDING_ASSIGNMENT.value
    delivery.booked_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.booked_at
    delivery.last_error = None
    delivery.last_payload = created

    logger.info(
        "noon Send task %s created for %s from %s (est. AED %s)",
        task_nr,
        order.order_number,
        pickup.reference or pickup.name,
        delivery.cost_total if delivery.cost_total is not None else "-",
    )

    await db.commit()
    return delivery


async def cancel_delivery(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Call off the rider when the order is cancelled.

    Only possible before pickup, and charged on production — which we accept
    rather than send a rider to a cancelled order.
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None or not delivery.courier_order_id:
        return delivery
    if delivery.courier_status in NOON_SEND_FAILED_STATUSES:
        return delivery
    if not is_enabled():
        delivery.last_error = "noon Send is not configured; cancel this task by hand"
        return delivery

    try:
        await provider.cancel_task(delivery.courier_order_id)
    except NoonSendError as exc:
        delivery.last_error = f"Could not cancel with noon Send: {exc}"
        logger.warning("noon Send cancel failed for %s: %s", order.order_number, exc)
        return delivery

    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = NoonSendStatusEnum.CANCELLED.value
    delivery.cancelled_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.cancelled_at
    delivery.cancel_party = "MERCHANT"
    delivery.cancel_reason = "order_cancelled"
    delivery.last_error = None
    return delivery


# ── inbound ───────────────────────────────────────────────────────────────────

#: noon Send status -> the order status it implies. Anything absent leaves the
#: order alone: a rider being assigned is still "packed and waiting", and an
#: `undelivered` parcel is a problem for an admin rather than a state the
#: customer's order should move into on its own.
_ORDER_STATUS_FOR: dict[str, OrderStatusEnum] = {
    NoonSendStatusEnum.PICKED_UP.value: OrderStatusEnum.OUT_FOR_DELIVERY,
    NoonSendStatusEnum.DELIVERED.value: OrderStatusEnum.DELIVERED,
}


def _event_id(payload: dict[str, Any]) -> str:
    """
    A stable id for an event that arrived without one.

    noon Send sends no event identifier, so dedup keys on the thing that is
    actually unique about a status change: the task, the status, and when it
    happened. A genuine retry reproduces all three; a real transition changes at
    least one.
    """
    return (
        f"noon_send:{payload.get('order_nr')}:"
        f"{payload.get('status_code')}:{payload.get('timestamp')}"
    )


async def _delivery_for(
    db: AsyncSession, payload: dict[str, Any]
) -> OrderDelivery | None:
    """The delivery this push is about, by task number or by our own reference."""
    task_nr = payload.get("order_nr")
    if task_nr:
        found = (
            (
                await db.execute(
                    select(OrderDelivery).where(
                        OrderDelivery.courier_order_id == str(task_nr),
                        OrderDelivery.provider == PROVIDER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if found is not None:
            return found

    # The task number can be missing on an early push, and our own order number
    # went out on the task as `order_reference`, so it is a reliable second key.
    reference = payload.get("order_reference")
    if not reference:
        return None
    return (
        (
            await db.execute(
                select(OrderDelivery)
                .join(Order, Order.id == OrderDelivery.order_id)
                .where(
                    Order.order_number == str(reference),
                    OrderDelivery.provider == PROVIDER,
                )
            )
        )
        .scalars()
        .first()
    )


async def handle_webhook(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate and apply one status push from noon Send."""
    status_code = str(payload.get("status_code") or "").strip().lower()
    if not payload.get("order_nr") and not payload.get("order_reference"):
        raise NoonSendError("Webhook names no task")

    inserted = await db.execute(
        pg_insert(WebhookEvent)
        .values(
            provider="noon_send",
            event_id=_event_id(payload)[:255],
            event_type=(status_code or "unknown")[:100],
            order_number=str(payload.get("order_reference") or "")[:30] or None,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    if inserted.rowcount == 0:
        logger.info("Duplicate noon Send webhook skipped: %s", _event_id(payload))
        return {"received": True, "duplicate": True}

    delivery = await _delivery_for(db, payload)
    if delivery is None:
        # A task we have no record of. Acknowledged and left alone rather than
        # retried at us forever.
        return {"received": True, "event_type": status_code, "matched": False}

    await apply_webhook(db, payload, delivery=delivery)
    return {"received": True, "event_type": status_code, "matched": True}


async def apply_webhook(
    db: AsyncSession,
    payload: dict[str, Any],
    delivery: OrderDelivery,
) -> OrderDelivery:
    """
    Fold one push into the delivery record, and move the order if it should.

    Out-of-order pushes are dropped rather than allowed to walk a delivered
    order back to "assigned", exactly as on the Lalamove side.

    Their `timestamp` is `YYYY-MM-DD HH:MM:SS` with no zone, and it is **UTC** —
    confirmed by reading `created_at` off a live task and comparing it against
    our own clock, which agreed to the second rather than being four hours out.
    `parse_time` reads a naive stamp as UTC, so the comparison below is like
    for like. If that ever changed to Gulf local time every push would arrive
    stamped four hours in the future, which would not reorder anything but
    would put `delivered_at` four hours late on every order.
    """
    updated_at = parse_time(payload.get("timestamp"))
    if (
        updated_at is not None
        and delivery.status_updated_at is not None
        and updated_at < delivery.status_updated_at
    ):
        logger.info(
            "Ignoring out-of-order noon Send webhook for %s (%s < %s)",
            delivery.courier_order_id,
            updated_at,
            delivery.status_updated_at,
        )
        return delivery

    delivery.last_payload = payload
    if task_nr := payload.get("order_nr"):
        delivery.courier_order_id = str(task_nr)

    status = str(payload.get("status_code") or "").strip().lower()
    if status and status != delivery.courier_status:
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_status = status
        moment = updated_at or datetime.now(timezone.utc)
        if status == NoonSendStatusEnum.PICKED_UP.value:
            delivery.picked_up_at = moment
        elif status == NoonSendStatusEnum.DELIVERED.value:
            delivery.delivered_at = moment
        elif status in NOON_SEND_FAILED_STATUSES:
            delivery.cancelled_at = moment
            delivery.cancel_reason = status
            # Nobody is bringing it. The order stays where it is so an admin can
            # re-dispatch, rather than the customer being told it is cancelled.
            delivery.last_error = (
                f"noon Send reported the task {status} — re-dispatch required"
            )

    if updated_at is not None:
        delivery.status_updated_at = updated_at
    elif status:
        delivery.status_updated_at = datetime.now(timezone.utc)

    await _advance_order(db, delivery)
    return delivery


async def handle_tracking_webhook(
    db: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    """
    Apply one rider position push.

    Not deduplicated and not journalled: these arrive every 15-30 seconds per
    active task, and a row per ping would bury the status events in the same
    table. The last position simply overwrites the one before it.
    """
    delivery = await _delivery_for(db, payload)
    if delivery is None:
        return {"received": True, "matched": False}
    apply_tracking(delivery, payload)
    return {"received": True, "matched": True}


def apply_tracking(delivery: OrderDelivery, payload: dict[str, Any]) -> None:
    details = payload.get("da_details") or {}
    latitude = decimal_or_none(details.get("latitude"))
    longitude = decimal_or_none(details.get("longitude"))
    if latitude is not None and longitude is not None:
        delivery.driver_latitude = latitude
        delivery.driver_longitude = longitude


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
    # Refunded, disputed and cancelled orders are settled. A late courier update
    # must not resurrect one.
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


async def refresh(db: AsyncSession, order_id: uuid.UUID) -> OrderDelivery | None:
    """
    Pull the current state of a task, for when a webhook never arrived.

    Their statuses only reach us by push, and a push that is lost is lost — so
    the admin's "refresh" button needs a way to ask.
    """
    delivery = await get_delivery(db, order_id)
    if delivery is None or not delivery.courier_order_id:
        return delivery
    if not is_enabled():
        delivery.last_error = "noon Send is not configured"
        return delivery

    try:
        details = await provider.get_task(delivery.courier_order_id)
    except NoonSendError as exc:
        delivery.last_error = f"Could not read the task from noon Send: {exc}"
        return delivery

    rider = details.get("da_details") or {}
    if rider:
        delivery.driver_name = rider.get("name") or delivery.driver_name
        delivery.driver_phone = rider.get("phone_number") or delivery.driver_phone
        location = rider.get("location") or {}
        delivery.driver_latitude = (
            decimal_or_none(location.get("latitude")) or delivery.driver_latitude
        )
        delivery.driver_longitude = (
            decimal_or_none(location.get("longitude")) or delivery.driver_longitude
        )
    if details.get("has_pod"):
        delivery.pod_status = "AVAILABLE"

    # No `order_reference`: the task details put our order number and their task
    # number in one composite `order_id` string ("MM-1001 - HG84NN…"), which is
    # not the reference a webhook carries and would not match if it were looked
    # up. The delivery row is passed directly, so nothing needs to match.
    await apply_webhook(
        db,
        {
            "order_nr": delivery.courier_order_id,
            "status_code": details.get("status_code"),
        },
        delivery=delivery,
    )
    return delivery
