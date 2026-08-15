"""
The bridge between an order and a noon Send task.

Same three rules as the Lalamove bridge — a courier failure is never a customer
failure, the price the customer pays comes from the zone map and not from here,
and a third-party zone behaves exactly as it did before — plus one of its own.

**Nobody tells us what a run cost.** noon Send has no quotation endpoint, the
create-task response carries no price, and neither do the task details. The only
cost figure that will ever exist for one of their tasks is the one this module
computes from their published rate card:

    AED 12 by bike / 25 by bulky car   for the first 10 km
      + 1.00 per km                    for  10-15 km
      + 1.50 per km                    for  15-20 km
      + 1.00                           during 12:00-15:00 and 19:00-22:00

Distance is straight line from the kitchen times `NOON_SEND_DETOUR_FACTOR`,
which is fitted (1.49x) against the sixteen Sharjah areas the live Lalamove rate
card was measured over. It is an estimate and it is labelled as one everywhere it
surfaces; treating it as a billed figure would be inventing precision we do not
have.

The vehicle tier is the whole argument. On a bike they beat Lalamove's
`17 + 0.70/km` at every distance in range, surge included — 12.45 against 22.29
on average across Sharjah Central, which turns a AED 15 fee from a loss into a
margin. In the bulky car product at AED 25 they lose at every distance in range,
so a zone that needed cars would be a zone that should have stayed on Lalamove.
Standard cakes go by bike.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderPayment
from app.models.order_delivery import (
    NOON_SEND_STATUS_RANK,
    NOON_SEND_FAILED_STATUSES,
    NoonSendStatusEnum,
    OrderDelivery,
)
from app.models.webhook_event import WebhookEvent
from app.services import (
    address_format,
    courier_reference,
    email_service,
    order_lifecycle,
)
from app.services.lalamove_service import (
    Estimate,
    PickupPoint,
    get_delivery,
    normalise_phone,
    parse_time,
    resolve_pickup,
)
from app.services.providers.noon_send_provider import (
    NoonSendError,
    degrees,
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

#: The shop's clock, which is what the surge windows are quoted against.
TZ = ZoneInfo(DELIVERY_TIMEZONE)


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


#: Peak windows, on the shop's clock, in which every band costs AED 1 more.
#: Both of ours matter: 58% of orders arrive in the evening one.
SURGE_WINDOWS: tuple[tuple[int, int], ...] = ((12, 15), (19, 22))

#: Their ceiling. The card has no band past 20 km, and our own
#: `NOON_SEND_MAX_DISTANCE_M` is stricter still — this is the honest edge of
#: what the published card can price rather than a limit we enforce.
RATE_CARD_MAX_KM = 20.0


def is_surge(at: datetime | None = None) -> bool:
    """Whether a run booked now falls in one of the peak windows."""
    local = (at or datetime.now(timezone.utc)).astimezone(TZ)
    return any(start <= local.hour < end for start, end in SURGE_WINDOWS)


def rate_card_cost(
    distance_km: float,
    *,
    bulky: bool = False,
    at: datetime | None = None,
) -> Decimal:
    """
    What noon Send charges for a run of this length.

    The bands are marginal, not selective: the base covers the first ten
    kilometres and each band prices only the distance inside it. Read the other
    way the card would price eleven kilometres below ten, which no courier means.

    Two things the base hides. The card has a **vehicle tier** — AED 12 on a bike
    against AED 25 for the bulky car product — and the whole case for this
    courier lives in that gap: on a bike they beat Lalamove at every distance in
    range, and in a car they lose at every distance in range. Standard cakes go
    by bike; `bulky` is here so that a large one can be priced honestly rather
    than silently costed as if it were not.

    And a **surge**: AED 1 on top across all bands during the peak windows. Small
    per order and easy to leave out, but the evening window is where most orders
    land, so leaving it out would understate the cost of the busiest hours.
    """
    base = float(settings.NOON_SEND_BULKY_BASE if bulky else settings.NOON_SEND_BASE)
    capped = min(distance_km, RATE_CARD_MAX_KM)

    if capped <= 10:
        total = base
    elif capped <= 15:
        total = base + (capped - 10) * 1.00
    else:
        total = base + 5.0 + (capped - 15) * 1.50

    if is_surge(at):
        total += float(settings.NOON_SEND_SURGE_AED)
    return Decimal(f"{total:.2f}")


async def pickup_for(db: AsyncSession, order: Order) -> PickupPoint | None:
    """
    Where this order's rider collects from: the branch that is making it.

    A thin wrapper, kept rather than inlined, so the serviceability check, the
    task and the cost estimate cannot disagree about the answer — a run quoted
    from Sharjah and booked from Dubai would be wrong by thirty kilometres.

    It used to be more than a wrapper. While the integration was proved against
    noon's staging fleet, one named account collected from a fixed Dubai outlet
    instead, because that fleet serves three Dubai outlets and cannot cross an
    emirate boundary. The real fleet has no such problem and the fixture is gone.
    """
    return await resolve_pickup(db, order.branch_id)


#: The partner limits noon Send reports for our own key, cached in process.
#: `None` until the first successful read; a failed read leaves it None and
#: every caller falls back to the configured numbers, so an outage narrows the
#: decision rather than breaking it.
_limits: dict[str, Any] | None = None


def invalidate_limits() -> None:
    global _limits
    _limits = None


async def partner_limits() -> dict[str, Any]:
    """
    What noon Send says this account may actually do.

    Asked once per process rather than assumed, because three different numbers
    claim to be the distance cap: the integration doc says the standard partner
    limit is 15 km, the rate card prices bands out to 20, and the staging
    partner answers 50. Only one of them is about our key, and it is this one.

    Also carries `cod_limit` and `prepaid_limit` in fils, which nothing else
    tells us and which reject a task outright when exceeded.
    """
    global _limits
    if _limits is not None:
        return _limits
    if not is_enabled():
        return {}
    try:
        _limits = await provider.get_configurations() or {}
    except Exception:  # pragma: no cover — network; the caller has a fallback
        logger.warning("Could not read noon Send partner limits")
        return {}
    return _limits


async def max_distance_m() -> int:
    """
    The drop-off ceiling: ours, always.

    20 km is the agreed commercial limit and the distance the rate card prices
    to, and it is what `Sharjah Central` is drawn against. `/configurations`
    reports whatever the *environment* is set up for — staging answers 50 km,
    which is a sandbox number and describes nothing about the real fleet — so
    letting it decide would either widen a zone nobody redrew or narrow one that
    is correct.

    Their answer is still worth reading, so a genuine disagreement shows up in
    the log rather than in a customer's fee. It never moves the ceiling.
    """
    ours = settings.NOON_SEND_MAX_DISTANCE_M
    reported = (await partner_limits()).get("distance_limit")
    try:
        theirs = int(reported)
    except (TypeError, ValueError):
        return ours
    if theirs < ours:
        logger.info(
            "noon Send reports a %d m limit against our %d m; keeping ours",
            theirs,
            ours,
        )
    return ours


async def estimate_for_point(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    address: str | None = None,
    branch_id: uuid.UUID | None = None,
    pickup: PickupPoint | None = None,
) -> tuple[Estimate | None, str | None]:
    """
    What a noon Send run to this point would cost us, or why we cannot say.

    Signature-compatible with `lalamove_service.estimate_for_point` so the
    checkout path does not have to know which courier a zone uses. Makes no
    network call — there is nothing to call — so unlike the Lalamove version it
    costs nothing and cannot time out.
    """
    pickup = pickup or await resolve_pickup(db, branch_id)
    if pickup is None:
        return None, "No pickup branch is configured"

    distance = road_distance_km(pickup.latitude, pickup.longitude, latitude, longitude)
    distance_m = int(distance * 1000)
    limit_m = await max_distance_m()
    if distance_m > limit_m:
        return None, (
            f"{distance:.1f} km is past noon Send's {limit_m / 1000:.0f} km limit"
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

    pickup = await pickup_for(db, order)
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
    limit_km = (await max_distance_m()) / 1000
    if distance > limit_km:
        return False, (
            f"Drop-off is about {distance:.1f} km away, past noon Send's "
            f"{limit_km:.0f} km limit"
        )

    # The money ceilings, which nothing but `/configurations` reports. A task
    # over either is rejected at creation, and on production a rejection after
    # a rider has been engaged is a cancellation we pay for. Staging answers
    # AED 300 for COD, which a two-tier cake passes comfortably.
    limits = await partner_limits()
    is_cod = (order.payment_method or "").lower() == "cod"
    if is_cod and limits.get("is_cod_blocked"):
        return False, "noon Send is not accepting cash on delivery for this account"
    outstanding = await outstanding_balance(db, order)
    ceiling = limits.get("cod_limit") if is_cod else limits.get("prepaid_limit")
    value = fils(outstanding if is_cod else (order.total or Decimal("0")))
    if ceiling and value > int(ceiling):
        kind = "cash on delivery" if is_cod else "prepaid"
        return False, (
            f"AED {value / 100:.2f} is over noon Send's {kind} ceiling of "
            f"AED {int(ceiling) / 100:.2f}"
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


def _declared_value(total: Decimal) -> int:
    """
    The prepaid amount, in fils, with a flat stand-in when it is nothing.

    noon Send requires one of `cod_value` and `prepaid_value` and treats zero as
    absent — `cod_prepaid_missing`, a 400 at task creation. An order can honestly
    total AED 0.00: a fully discounted basket with the delivery fee waived does,
    and the integration was proved on exactly those.

    AED 1.00 stands in, and deliberately a flat one — deriving something
    plausible from the order would put a number in noon's records that nobody
    paid. It is as true as anything else for a bag that was given away, and the
    alternative is a refusal and a fall back to Lalamove. Nothing here can cause
    money to be collected: only `cod_value` does that, and it stays zero.
    """
    value = fils(total)
    return value if value > 0 else 100


def build_task(
    order: Order, outstanding: Decimal, reference: str
) -> tuple[Task | None, str | None]:
    """
    Turn an order's shipping snapshot into a task, or say why it cannot be one.

    Mirrors `lalamove_service.build_drop`: the reason is written to the delivery
    row so an admin reads "no reachable phone number" rather than a 422 about a
    field they have never heard of. Reads nothing but plain columns, for the
    same reason — `outstanding` and `reference` are passed in rather than
    derived here.
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

    # `address_format.one_line`, which is also what the rider's stop, the
    # register's ticket and the customer's email are built from. Unit first: a
    # formatted Google address gets a rider to the building and the flat number
    # finishes the job.
    text = address_format.one_line(address) or ""
    if len(text) < 5:
        return None, "Order has no usable street address"

    name = address_format.recipient_name(address) or ""

    total = Decimal(str(order.total or 0))
    is_cod = (order.payment_method or "").lower() == "cod" and outstanding > 0

    # What the customer asked for, and nothing else.
    #
    # This used to lead with `Order {reference} · Ref {order_number} · Unit
    # {unit}`. Every one of those is already in the payload: the reference *is*
    # `order_reference`, and the unit is the first thing in the address string
    # above. A rider opening the task read three fields they already had before
    # reaching the one thing only the customer could tell them — so a gate code
    # sat at the end of a line noon truncates at 250 characters.
    notes = str(order.notes or "").strip()

    return (
        Task(
            order_reference=reference,
            drop_off_address={
                "lat": round(latitude * 10_000_000),
                "lng": round(longitude * 10_000_000),
                "address": text[:10_000],
                "contact_name": (name or order.order_number)[:100],
                "contact_phone_number": phone,
                "country_code": "ae",
                "city": str(address.get("city") or "Sharjah")[:100],
            },
            prepaid_value=0 if is_cod else _declared_value(total),
            cod_value=fils(outstanding) if is_cod else 0,
            delivery_notes=notes[:250],
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

    pickup = await pickup_for(db, order)
    if pickup is None:
        delivery.last_error = "No pickup branch is configured"
        return delivery
    if not pickup.noon_send_outlet_code:
        delivery.last_error = (
            f"Branch {pickup.reference or pickup.name} has no noon Send outlet "
            "code — register it and set it on the branch"
        )
        return delivery

    # Seven digits for the rider to quote, instead of `MM-20260805-007`. Drawn
    # before the task is built so the notes and the reference agree, and falling
    # back to the order number rather than blocking a dispatch over a label.
    reference = await courier_reference.assign(db, delivery) or order.order_number

    task, reason = build_task(order, await outstanding_balance(db, order), reference)
    if task is None:
        delivery.last_error = reason
        return delivery

    try:
        created = await provider.create_task(
            order_reference=task.order_reference,
            # The branch that actually resolved, not a global. When a second
            # kitchen starts delivering this is already the right value.
            outlet_code=pickup.noon_send_outlet_code,
            # Sent when the branch has one. Absent is fine — the outlet code is
            # what identifies the pickup.
            outlet_address_code=pickup.noon_send_outlet_address_code or None,
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

    # From the branch this task was actually created against. Without the
    # `branch_id` this falls back to the globally configured pickup, so a second
    # kitchen would dispatch from one place and be costed from another.
    estimate, _ = await estimate_for_point(
        db,
        float(order.shipping_address_snapshot["latitude"]),
        float(order.shipping_address_snapshot["longitude"]),
        branch_id=order.branch_id,
        # The outlet the task was actually created against, so the run is costed
        # over the distance a rider really travels.
        pickup=pickup,
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
#: order alone: a rider being assigned is still "packed and waiting".
#:
#: `undelivered` is here rather than left as a note on the delivery row. It used
#: not to be, and the result was MM-20260805-006: the order read
#: `out_for_delivery` — the cake is coming — while the courier record said a
#: rider had stood at the door and taken it away again. Only one of those was
#: true, and it was not the one on the screen.
_ORDER_STATUS_FOR: dict[str, OrderStatusEnum] = {
    NoonSendStatusEnum.PICKED_UP.value: OrderStatusEnum.OUT_FOR_DELIVERY,
    NoonSendStatusEnum.DELIVERED.value: OrderStatusEnum.DELIVERED,
    NoonSendStatusEnum.UNDELIVERED.value: OrderStatusEnum.UNDELIVERED,
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

    # The task number can be missing on an early push, and whatever we sent as
    # `order_reference` comes back on every one, so it is a reliable second key.
    reference = str(payload.get("order_reference") or "")
    if not reference:
        return None

    # The short reference is what goes out now. Tasks created before it existed
    # carry the order number instead, and a dispatch that could not draw a free
    # number falls back to it too — so both are looked up, short one first.
    found = (
        (
            await db.execute(
                select(OrderDelivery).where(
                    OrderDelivery.courier_reference == reference,
                    OrderDelivery.provider == PROVIDER,
                )
            )
        )
        .scalars()
        .first()
    )
    if found is not None:
        return found

    return (
        (
            await db.execute(
                select(OrderDelivery)
                .join(Order, Order.id == OrderDelivery.order_id)
                .where(
                    Order.order_number == reference,
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
    task back to "assigned" — but by rank rather than by clock. Their published
    webhook contract is three fields, `order_nr`, `status_code` and
    `order_reference`, with no timestamp at all, so there is nothing to compare.
    Reading one anyway meant the guard was never once armed.

    A `timestamp` is still read when present, because their documentation
    describes one and the tracking webhook does carry it. It is naive
    `YYYY-MM-DD HH:MM:SS` and it is **UTC** — confirmed by reading `created_at`
    off a live task against our own clock, which agreed to the second rather
    than being four hours out. It is used to stamp the record, never to decide
    ordering.
    """
    updated_at = parse_time(payload.get("timestamp"))
    status = str(payload.get("status_code") or "").strip().lower()
    if delivery.courier_status and status:
        arriving = NOON_SEND_STATUS_RANK.get(status, -1)
        current = NOON_SEND_STATUS_RANK.get(delivery.courier_status, -1)
        if arriving < current:
            logger.info(
                "Ignoring out-of-order noon Send webhook for %s (%s after %s)",
                delivery.courier_order_id,
                status or "(none)",
                delivery.courier_status,
            )
            return delivery

    delivery.last_payload = payload
    if task_nr := payload.get("order_nr"):
        delivery.courier_order_id = str(task_nr)

    if status and status != delivery.courier_status:
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_status = status
        moment = updated_at or datetime.now(timezone.utc)
        if status == NoonSendStatusEnum.ASSIGNED.value:
            # Their status push is three fields — task, status, reference — so
            # the rider's name is not in it. The integration doc is explicit
            # that "once a Delivery Agent is assigned, their contact details
            # will be shared in the task detail", so the detail is what we ask.
            # Without this the counter was told "a rider is on the way" with no
            # name and no number to call.
            await _fill_rider_details(delivery)
            await _announce_rider(db, delivery)
        # `picked_up_at` and `delivered_at` used to be written here too. They
        # were the same two moments `order_status_events` records as
        # `out_for_delivery` and `delivered` — the same fact in two tables, and
        # the customer's timeline read one while the admin's read the other.
        # `_advance_order` carries `moment` into the history instead.
        if status in NOON_SEND_FAILED_STATUSES:
            delivery.cancelled_at = moment
            delivery.cancel_reason = status
            # Nobody is bringing it. The order is not cancelled — it is paid for
            # and boxed — so this is a flag for the admin, not an ending.
            delivery.last_error = (
                f"noon Send reported the task {status} — re-dispatch required"
            )

    if updated_at is not None:
        delivery.status_updated_at = updated_at
    elif status:
        delivery.status_updated_at = datetime.now(timezone.utc)

    # Moves the order and writes to the customer, `undelivered` included.
    # A failed handover used to be announced separately here, because the order
    # stayed put and `_advance_order` had nothing to do. It has a status of its
    # own now, so there is one path again — and only one email.
    await _advance_order(db, delivery, at=updated_at)

    return delivery


async def _fill_rider_details(delivery: OrderDelivery) -> None:
    """
    Put a name and a number to the rider, from the task detail.

    Best-effort: the webhook must still answer 200. Their doc warns the details
    are "only available for active tasks or recently completed ones", so this is
    called on assignment rather than left until someone opens the admin.
    """
    if not delivery.courier_order_id or not is_enabled():
        return
    try:
        details = await provider.get_task(str(delivery.courier_order_id))
    except Exception:  # pragma: no cover — network, never worth failing on
        logger.warning(
            "Could not read the noon Send task %s for rider details",
            delivery.courier_order_id,
        )
        return
    rider = details.get("da_details") or {}
    if not rider:
        return
    delivery.driver_name = rider.get("name") or delivery.driver_name
    delivery.driver_phone = rider.get("phone_number") or delivery.driver_phone
    location = rider.get("location") or {}
    delivery.driver_latitude = (
        degrees(location.get("latitude")) or delivery.driver_latitude
    )
    delivery.driver_longitude = (
        degrees(location.get("longitude")) or delivery.driver_longitude
    )


async def _announce_rider(db: AsyncSession, delivery: OrderDelivery) -> None:
    """Same as the Lalamove side: best-effort, never fails the webhook."""
    from app.services import push_service

    order = (
        (await db.execute(select(Order).where(Order.id == delivery.order_id)))
        .scalars()
        .first()
    )
    if order is None or not getattr(order, "branch_id", None):
        return
    try:
        await push_service.notify_rider_assigned(
            db, order, rider_name=delivery.driver_name
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("Could not announce the rider for %s", order.order_number)


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
    """
    Move the rider's pin.

    The coordinates are nested under `da_details.location` and encoded the same
    way we send ours — degrees times 10^7, as strings. This read them one level
    too high and unconverted, so every tracking push was a silent no-op: the
    lookup missed, the guard below was never satisfied, and nothing was written
    or logged. Two real pushes arrived that way before anyone looked.
    """
    details = payload.get("da_details") or {}
    # Both shapes, because noon uses both. The task detail nests the pair under
    # `location`; the tracking webhook puts them straight on `da_details`. This
    # read only the first, so every tracking push for a live order was a no-op —
    # thirteen of them arrived in eight minutes and moved nothing.
    location = details.get("location") or details
    latitude = degrees(location.get("latitude"))
    longitude = degrees(location.get("longitude"))
    if latitude is not None and longitude is not None:
        delivery.driver_latitude = latitude
        delivery.driver_longitude = longitude


async def _order_of(db: AsyncSession, delivery: OrderDelivery) -> Order | None:
    """
    The order this delivery belongs to, with its lines loaded.

    The lines are loaded because everything this function feeds ends up at the
    mailer, and `OrderResponse` reads `order.items`. Selected bare, that is a
    `MissingGreenlet` rather than a query, and it silently cost four customer
    emails on 2026-08-05. `email_service.notify_order` re-reads defensively as
    well; this is the same fact stated where the select happens.
    """
    return (
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


async def _advance_order(
    db: AsyncSession, delivery: OrderDelivery, *, at: datetime | None = None
) -> None:
    """Move the order to match the courier, stamped with the courier's clock.

    `at` is null far more often here than for Lalamove: noon Send's status
    webhook carries `order_nr`, `status_code` and `order_reference` and no
    usable timestamp at all, which is why `NOON_SEND_STATUS_RANK` exists to
    order them. The history then records when we heard, which is the only
    moment there is.
    """
    target = _ORDER_STATUS_FOR.get(delivery.courier_status or "")
    if target is None:
        return

    order = await _order_of(db, delivery)
    if order is None:
        return

    # One rulebook, not a private copy of it. This guard used to be hand-rolled
    # here — and had drifted: it still allowed `undelivered` back out to
    # `out_for_delivery` after the shop decided (see the map's own note) that
    # `undelivered` is an ending, not a detour. The map also refuses stale news
    # about settled orders and a parcel "coming back undelivered" before anyone
    # picked it up, which the old guard spelled out by hand.
    #
    # Going through `transition` is also what closes MM-level gap this module
    # shipped with: a courier marking an order undelivered now triggers the
    # same automatic refund an admin's click always did — previously the email
    # below promised a refund nothing had initiated.
    #
    # The courier's own word for what happened goes in the note, so the history
    # records `picked_up` next to `out_for_delivery` rather than only our
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
    # transitions only ever happen because a courier said so, so this is the
    # only place that knows they happened — and "out for delivery" is the email
    # that carries the live tracking link, which is worth nothing an hour late.
    await email_service.notify_order(db, order)


async def refresh(db: AsyncSession, order_id: uuid.UUID) -> OrderDelivery | None:
    """
    Pull the current state of a task, for when a webhook never arrived.

    noon Send's statuses only reach us by push, and a push that is lost is lost:
    unlike Lalamove they do not retry, and unlike Stripe there is nothing to
    replay. Without a way to ask, a rider could deliver an order and the shop
    would still be looking at "assigned" tomorrow. Reachable from the admin's
    delivery panel — see `POST /orders/{n}/delivery/refresh`.
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
            degrees(location.get("latitude")) or delivery.driver_latitude
        )
        delivery.driver_longitude = (
            degrees(location.get("longitude")) or delivery.driver_longitude
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
