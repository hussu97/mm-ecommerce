"""
The bridge between an order and a Slider delivery.

Same three rules as the other two bridges — a courier failure is never a
customer failure, the price the customer pays comes from the zone map and not
from here, and a third-party zone behaves exactly as it did before — plus two of
its own.

**The vehicle is one decision, made once.** Slider prices a bike and a car
differently and there is no quotation id to carry a choice from the estimate
into the booking, so if the two computed it separately the quote and the invoice
would disagree. `vehicle_for` is the single pure function both call.

**They have no coverage endpoint, and their fare endpoint is not one.**
`/deliveries/fare` priced Riyadh, Muscat and Liwa at 345 km. The only thing that
ever says "we cannot reach this address" is a 422 when the delivery is created,
which is why the fall back to another courier at that moment is load-bearing
rather than defensive: a refusal here would strand a paid, packed order.

Slider was brought in for dispatch accuracy, not for price. Across the Ajman,
Dubai and Umm al-Quwain zones its car costs about AED 0.94 an order more than
Lalamove — Ajman is cheaper on every one of its eleven areas, Dubai and Umm
al-Quwain are dearer, and the difference is the price of the reliability.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.order_delivery import (
    SLIDER_FAILED_STATUSES,
    SLIDER_STATUS_RANK,
    OrderDelivery,
    SliderStatusEnum,
)
from app.models.webhook_event import WebhookEvent
from app.services import (
    address_format,
    courier_reference,
    driver_assignment,
    driver_routing,
    email_service,
    geo,
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
from app.services.providers.slider_provider import SliderError, aed, provider

logger = logging.getLogger(__name__)

__all__ = [
    "PROVIDER",
    "apply_webhook",
    "cancel_delivery",
    "dispatch_order",
    "estimate_for_point",
    "handle_webhook",
    "is_enabled",
    "may_serve",
    "refresh",
    "road_distance_km",
    "same_emirate",
    "vehicle_for",
]

PROVIDER = FulfilmentProviderEnum.SLIDER.value

BIKE = "bike"
CAR = "car"

#: What Slider will hand over on delivery, in AED, by how the order was paid.
#:
#: Hard refusals rather than something to discover at creation time: a rejection
#: after a rider has been engaged is a cancellation we pay for, and these two
#: numbers are commercial terms rather than anything their API reports.
COD_CASH_CEILING = Decimal("350.00")
COD_CARD_CEILING = Decimal("500.00")

#: Checkout re-quotes on every pin move and a courier run's price does not
#: depend on the basket, so identical points inside this window reuse the last
#: answer. A failure is held far more briefly: every Slider zone is fixed-fee
#: today, so a stale failure costs only margin data — but that will not always
#: be true, and the shape that is right when it stops being true is this one.
_QUOTE_CACHE_SECONDS = 120
_FAILURE_CACHE_SECONDS = 15

_quote_cache: dict[
    tuple[uuid.UUID | None, float, float],
    tuple[float, Estimate | None, str | None],
] = {}


def clear_caches() -> None:
    _quote_cache.clear()


def is_enabled() -> bool:
    """Whether we can create a delivery at all.

    Without a key a `slider` zone still prices and sells normally — it just
    dispatches through whoever carries that zone's customers today.
    """
    return provider.is_configured


# ── the vehicle rule ──────────────────────────────────────────────────────────


#: The seven emirates, longest first so that a prefix search cannot stop early
#: on a shorter name and so "Ras al-Khaimah North" resolves to Ras al-Khaimah.
EMIRATES: tuple[str, ...] = (
    "Ras al-Khaimah",
    "Umm al-Quwain",
    "Abu Dhabi",
    "Fujairah",
    "Sharjah",
    "Ajman",
    "Dubai",
)


def _squash(value: str | None) -> str:
    """Lower-case letters and digits only.

    None of these strings are ours: `branches.city` is typed by an admin and the
    drop's is whatever the address form captured, so "Umm al-Quwain", "Umm Al
    Quwain" and "umm al quwain" have to mean one place.
    """
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def emirate_of(value: str | None) -> str:
    """
    The emirate a city **or a zone name** names, or "" for neither.

    Two shapes of string reach the vehicle rule and they are not the same. A
    dispatch has an order, so it has `shipping_address_snapshot["city"]` — a
    place name. A checkout quote has only a pin, and the thing that knows where
    that pin is is the polygon it landed in, whose name is `Sharjah Core` or
    `Umm al-Quwain City`. Both have to reach the same answer, or the tier in the
    quote is not the tier on the booking — which is the one failure this
    module's single `vehicle_for` exists to prevent.

    Every zone name begins with its emirate by construction: the map generator
    enforces it because `delivery_service.public_zone_name` maps a zone to its
    emirate the same way, and anything else leaks a raw band name to a browser.
    So the prefix is a fact about the name rather than a guess about the text.
    """
    squashed = _squash(value)
    for emirate in EMIRATES:
        if squashed.startswith(_squash(emirate)):
            return emirate
    return ""


def same_emirate(pickup: str | None, drop: str | None) -> bool:
    """
    Whether these two places are in the same emirate.

    Either side may be a city or a zone name; both go through `emirate_of`
    first, so a `Sharjah Core` quote and a `Sharjah` dispatch agree. A value
    that names no emirate is not a match — "we do not know" must fall to the
    car, which is the vehicle allowed to cross.
    """
    left, right = emirate_of(pickup), emirate_of(drop)
    return bool(left) and left == right


def vehicle_for(*, in_same_emirate: bool, road_km: float | None) -> str:
    """
    `bike` or `car`, and the whole rule.

    > A bike if the drop is in the **same emirate as the pickup** *and* the
    > route is no more than `SLIDER_BIKE_MAX_KM` **road** kilometres. A car
    > otherwise.

    Both conditions. A bike may not cross an emirate boundary, so from the
    Sharjah kitchen the bike tier is only ever usable inside Sharjah, however
    short the run.

    ⚠️ Slider's own API disagrees: `/deliveries/fare` offered a bike for
    Sharjah→Ajman at 12 km and for nine Sharjah→Dubai routes out to 34.4 km.
    Either they do allow it or they are offering a vehicle they cannot
    dispatch, and nobody has answered that in writing. This takes the stricter
    reading, which costs roughly AED 5 an order across Ajman and Dubai — the
    wrong guess in this direction costs money, and the wrong guess in the other
    strands a cake.

    `road_km` is **road** distance. An unknown distance takes the car: not
    knowing how far it is is not a reason to send the vehicle with the limit on
    it.
    """
    if not in_same_emirate:
        return CAR
    if road_km is None:
        return CAR
    return BIKE if road_km <= settings.SLIDER_BIKE_MAX_KM else CAR


def road_distance_km(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """
    Roughly how far a rider actually drives between two points.

    Straight line times the factor the fare survey measured across its 97 areas
    — 1.44 mean, 1.24 median. Used **only** when Slider has not told us a
    distance themselves: their fare response carries the road distance they
    bill against, and a measured number beats a fitted one every time.

    The gap between the two is exactly why `vehicle_for` takes road kilometres
    and says so. 35 road km is about 24 km crow-flies, and comparing a 35 km
    threshold against a straight line would put half of Dubai on a bike.
    """
    return (
        geo.straight_line_km(lat_a, lng_a, lat_b, lng_b) * settings.SLIDER_DETOUR_FACTOR
    )


def _stop(
    *,
    latitude: float,
    longitude: float,
    address: str,
    phone: str = "",
    directions: str = "",
) -> dict[str, Any]:
    """One end of a run, in the shape Slider wants it.

    Their reference names four fields on a stop — `address`, `latitude`,
    `longitude`, `directions`, `contact_number` — and no contact name. What we
    used to send (`contact_phone`, `contact_name`) was neither read nor
    rejected, which is the worst of both: a rider with no number to call and a
    request that looked like it worked.

    `directions` is where a customer's note belongs. It is the only free-text
    field a rider is shown, and the fare/create endpoints have no `notes`.
    """
    stop: dict[str, Any] = {
        "latitude": round(float(latitude), 7),
        "longitude": round(float(longitude), 7),
        "address": address[:250],
    }
    if phone:
        stop["contact_number"] = phone
    if directions:
        stop["directions"] = directions[:250]
    return stop


def _fare(payload: dict[str, Any], vehicle: str) -> tuple[Decimal | None, float | None]:
    """
    The price and the road distance out of one fare response, for one vehicle.

    `vehicles` is a **list** of `{vehicle_type, is_available,
    unavailable_reason, delivery_fee}`, matched on `vehicle_type`. It was read
    here as a mapping keyed by vehicle, and the money as `fare`/`total`/
    `amount`, so every tier priced as nothing and every Slider zone quietly
    fell back to the courier it was meant to replace. An unavailable tier does
    price as nothing, which is why the bug looked like ordinary behaviour.
    """
    tiers = payload.get("vehicles")
    block = None
    if isinstance(tiers, list):
        block = next(
            (
                t
                for t in tiers
                if isinstance(t, dict)
                and str(t.get("vehicle_type") or "").strip().lower() == vehicle
            ),
            None,
        )
    elif isinstance(tiers, dict):
        # Tolerated, not expected. Their reference is unambiguous that this is a
        # list; a mapping here would mean the shape changed under us.
        block = tiers.get(vehicle)
    if not isinstance(block, dict):
        return None, None
    if block.get("is_available") is False:
        return None, None

    cost = aed(block.get("delivery_fee"))
    # `distance_km` belongs to the run, not to a tier: it sits at the top level
    # of the response and is the same number for bike and car.
    distance = payload.get("distance_km", block.get("distance_km"))
    try:
        distance_km = float(distance) if distance is not None else None
    except (TypeError, ValueError):
        distance_km = None
    return cost, distance_km


async def _quote(
    pickup: PickupPoint, latitude: float, longitude: float, address: str | None
) -> dict[str, Any]:
    """One fare call, both tiers, as Slider answered it."""
    return await provider.fare(
        pickup=_stop(
            latitude=pickup.latitude,
            longitude=pickup.longitude,
            address=pickup.address,
        ),
        delivery=_stop(
            latitude=latitude,
            longitude=longitude,
            address=address or "",
        ),
    )


async def estimate_for_point(
    db: AsyncSession,
    latitude: float,
    longitude: float,
    address: str | None = None,
    branch_id: uuid.UUID | None = None,
    pickup: PickupPoint | None = None,
    drop_emirate: str | None = None,
) -> tuple[Estimate | None, str | None]:
    """
    What a Slider run to this point would cost us, or why we cannot say.

    Signature-compatible with the other two `estimate_for_point`s so the
    checkout path does not have to know which courier a zone uses.

    Quoted on the vehicle `vehicle_for` chooses, which is the same function
    `dispatch_order` calls — so the tier in the estimate is the tier on the
    booking. Slider issues no quotation id to carry that choice across, so if
    the two decided separately they would eventually decide differently, and the
    first anyone would know is an invoice that did not match a quote.
    """
    if not is_enabled():
        return None, None

    # ~11 m of precision — finer than a building, coarser than the jitter a
    # dragged pin produces.
    #
    # Keyed on everything that moves the answer, which is more than the pin. The
    # origin, because the same doorstep costs a different amount from a
    # different kitchen — and the resolved **emirate**, because it decides the
    # vehicle and the vehicle decides the fare. Without it a `Sharjah Core`
    # quote and a bare-pin quote at the same coordinates would share one entry,
    # and one of the two would be shown the other tier's price.
    key = (
        pickup.reference if pickup is not None else branch_id,
        round(latitude, 4),
        round(longitude, 4),
        emirate_of(drop_emirate),
    )
    cached = _quote_cache.get(key)
    if cached:
        ttl = _QUOTE_CACHE_SECONDS if cached[1] is not None else _FAILURE_CACHE_SECONDS
        if time.monotonic() - cached[0] < ttl:
            return cached[1], cached[2]

    pickup = pickup or await resolve_pickup(db, branch_id)
    if pickup is None:
        return None, "No pickup branch is configured"

    estimate: Estimate | None = None
    error: str | None = None
    try:
        payload = await _quote(pickup, latitude, longitude, address)
    except SliderError as exc:
        error = str(exc)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("Unexpected error quoting Slider")
        error = f"Slider quote failed: {exc}"
    else:
        # Their measured road distance first, ours only if they did not say.
        # Theirs is what they bill against, which is the number the vehicle rule
        # is actually about.
        _, bike_km = _fare(payload, BIKE)
        _, car_km = _fare(payload, CAR)
        distance_km = (
            bike_km
            or car_km
            or road_distance_km(pickup.latitude, pickup.longitude, latitude, longitude)
        )
        vehicle = vehicle_for(
            in_same_emirate=same_emirate(pickup.emirate, drop_emirate),
            road_km=distance_km,
        )
        cost, _ = _fare(payload, vehicle)
        if cost is None:
            error = f"Slider quoted no {vehicle} fare for this address"
        else:
            estimate = Estimate(
                cost=cost,
                currency="AED",
                distance_m=int(distance_km * 1000),
                # No quotation id exists to reference, ever. Left empty rather
                # than filled with something invented, so a row carrying one
                # came from a courier that actually issued it.
                quotation_id=None,
            )

    _quote_cache[key] = (time.monotonic(), estimate, error)
    return estimate, error


# ── the per-order gate ────────────────────────────────────────────────────────


def _drop_emirate(order: Order, delivery: OrderDelivery | None = None) -> str:
    """
    Which emirate this order is going to, from the most reliable thing we have.

    The **zone** first. It is the polygon the pin actually landed in, it is what
    the checkout quoted against, and it is decided by geometry rather than
    typing. The address's `city` is a customer's or a geocoder's word for the
    same place and can disagree with the pin — and a disagreement here is a
    quote on one vehicle and a booking on the other, which is the failure this
    module's single `vehicle_for` exists to prevent.

    The city is the fallback, for a delivery row written before zones carried a
    name or against a map that has since been deleted.
    """
    zone = emirate_of(getattr(delivery, "zone_name", None))
    if zone:
        return zone
    return str((order.shipping_address_snapshot or {}).get("city") or "").strip()


async def may_serve(db: AsyncSession, order: Order) -> tuple[bool, str | None]:
    """
    Whether this order may be offered to Slider at all, and why not.

    Decided before we call them, because their only refusal is a 422 after the
    delivery has been created — and on production a rejection once a rider has
    been engaged is a cancellation we pay for.

    The money ceilings are commercial terms rather than anything the API
    reports, so they live here as hard refusals: cash on delivery up to AED 350,
    card up to AED 500.
    """
    address = order.shipping_address_snapshot or {}
    try:
        float(address["latitude"])
        float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return False, "Order has no delivery coordinates"

    pickup = await resolve_pickup(db, order.branch_id)
    if pickup is None:
        return False, "No pickup branch is configured"

    # The cash ceiling is measured against what the rider will actually be
    # handed, not against the order's total. Those are the same number on an
    # unpaid COD order and are not the same on a part-paid one — and it is
    # `outstanding` that `dispatch_order` puts in `cod_amount`, so checking the
    # total would be checking a figure nobody is carrying. The card ceiling is
    # about the value of the parcel, which is the total.
    is_cod = (order.payment_method or "").lower() == "cod"
    if is_cod:
        value, ceiling, kind = (
            await _outstanding(db, order),
            COD_CASH_CEILING,
            "cash on delivery",
        )
    else:
        value, ceiling, kind = Decimal(str(order.total or 0)), COD_CARD_CEILING, "card"
    if value > ceiling:
        return False, (
            f"AED {value:.2f} is over Slider's {kind} ceiling of AED {ceiling:.2f}"
        )
    return True, None


# ── dispatch ──────────────────────────────────────────────────────────────────


def build_stops(
    order: Order, pickup: PickupPoint
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """
    The two ends of the run, or why this order cannot be one.

    Mirrors `lalamove_service.build_drop` and `noon_send_service.build_task`:
    the reason is written to the delivery row so an admin reads "no reachable
    phone number" rather than a 422 about a field they have never heard of.
    """
    address = order.shipping_address_snapshot or {}
    try:
        latitude = float(address["latitude"])
        longitude = float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None, "Order has no delivery coordinates"

    phone = normalise_phone(str(address.get("phone") or ""))
    if not phone:
        return None, None, "Order has no reachable phone number"

    # `address_format.one_line`, which is also what the rider's stop, the
    # register's ticket and the customer's email are built from. Unit first: a
    # formatted Google address gets a rider to the building and the flat number
    # finishes the job.
    text = address_format.one_line(address) or ""
    if len(text) < 5:
        return None, None, "Order has no usable street address"

    # No contact name anywhere: their stop has `contact_number` and nothing to
    # carry a name in. The recipient is identified to the rider by the address
    # and by `display_order_id`.
    return (
        _stop(
            latitude=pickup.latitude,
            longitude=pickup.longitude,
            address=pickup.address,
            phone=pickup.phone,
        ),
        _stop(
            latitude=latitude,
            longitude=longitude,
            address=text,
            phone=phone,
            # What the customer asked for, on the stop the rider is standing at.
            directions=str(order.notes or "").strip(),
        ),
        None,
    )


async def dispatch_order(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Create one Slider delivery for one order.

    Returns the delivery row with `courier_order_id` set on success, and with
    `last_error` set and no id on failure — `courier_service` decides whether
    that failure is worth handing to another courier. Nothing here touches
    `dispatch_attempts` or `next_attempt_at`, retries internally, or stamps the
    order packed: `courier_service._record_outcome` owns all of that, once, on
    the way out, so a rung of the ladder cannot be skipped by whichever provider
    happened to answer.

    Committed on the success path for the same reason both other dispatchers
    commit: a rider has been engaged outside our transaction and cannot be
    rolled back with it, so losing the delivery id while their job keeps
    existing would put two riders on one cake. It is the deliberate exception to
    "services flush, the request commits".
    """
    delivery = await get_delivery(db, order.id)
    if delivery is None:
        return None

    pickup = await resolve_pickup(db, order.branch_id)
    if pickup is None:
        delivery.last_error = "No pickup branch is configured"
        return delivery

    collect, drop, reason = build_stops(order, pickup)
    if collect is None or drop is None:
        delivery.last_error = reason
        return delivery

    # Seven digits for the rider to quote, instead of `MM-20260805-007`. Drawn
    # before the booking so the notes and the reference agree, and falling back
    # to the order number rather than blocking a dispatch over a label.
    reference = await courier_reference.assign(db, delivery) or order.order_number

    # One fare call, and it does three jobs: it picks the vehicle, it prices the
    # run, and its distance is the one Slider bills against. A failure here is
    # not fatal — the booking still has to go out, on the vehicle the fitted
    # factor chooses.
    quoted: dict[str, Any] = {}
    try:
        quoted = await _quote(
            pickup, float(drop["latitude"]), float(drop["longitude"]), drop["address"]
        )
    except Exception:  # noqa: BLE001 — a missing quote must not stop a dispatch
        logger.warning(
            "Could not quote Slider for %s; booking anyway", order.order_number
        )

    _, bike_km = _fare(quoted, BIKE)
    _, car_km = _fare(quoted, CAR)
    distance_km = (
        bike_km
        or car_km
        or road_distance_km(
            pickup.latitude,
            pickup.longitude,
            float(drop["latitude"]),
            float(drop["longitude"]),
        )
    )
    vehicle = vehicle_for(
        in_same_emirate=same_emirate(pickup.emirate, _drop_emirate(order, delivery)),
        road_km=distance_km,
    )

    outstanding = await _outstanding(db, order)
    is_cod = (order.payment_method or "").lower() == "cod" and outstanding > 0

    try:
        created = await provider.create_delivery(
            # Ours, and the whole of their idempotency: there is no idempotency
            # header in their reference, so a retry after a timeout is the same
            # delivery to them only because this field repeats.
            order_id=reference,
            # What the rider is shown.
            display_order_id=order.order_number,
            vehicle=vehicle,
            pickup=collect,
            dropoff=drop,
            cod_amount=float(outstanding) if is_cod else 0.0,
            cod_type="cash",
        )
    except SliderError as exc:
        delivery.last_error = f"Slider: {exc}"
        logger.warning("Slider dispatch failed for %s: %s", order.order_number, exc)
        return delivery
    except Exception as exc:  # pragma: no cover — defensive
        delivery.last_error = f"Slider dispatch failed: {exc}"
        logger.exception("Unexpected error dispatching %s", order.order_number)
        return delivery

    # Their handle is `order_number`, a number. `id` / `delivery_id` /
    # `reference` are not fields they return, so this used to find nothing and
    # abandon a booking that had in fact been made. `order_id` is our own
    # reference echoed back, deliberately not used as their id.
    delivery_id = created.get("order_number")
    if not delivery_id:
        delivery.last_error = "Slider accepted the delivery but returned no id"
        return delivery

    if delivery.courier_order_id:
        delivery.previous_courier_order_ids = [
            *(delivery.previous_courier_order_ids or []),
            delivery.courier_order_id,
        ]

    # **What they assigned, not what we asked for.** `vehicle_type` on the
    # create response is authoritative: asking for a bike and being given a car
    # is a booking they accepted, not one they refused, and it is priced as a
    # car. Recording the request would put "bike" on a row carrying a car's
    # fare — an inconsistency that lands squarely in the margin figure the
    # pilot exists to produce.
    assigned = str(created.get("vehicle_type") or "").strip().lower() or vehicle

    # Slider's own number or nothing. A booking that priced nowhere leaves the
    # cost columns empty rather than filling them with something computed:
    # unlike noon Send, who publish a rate card and no API, Slider quotes — so a
    # figure on this row is always one of theirs, and the margin report can be
    # read as billed rather than guessed.
    #
    # The create response wins over the quote. The quote priced the tier we
    # asked for; the create says what they will actually bill, and when those
    # two disagree it is because they substituted the vehicle underneath us.
    cost = aed(created.get("fare") or created.get("total"))
    if cost is None:
        cost, _ = _fare(quoted, assigned)
    # Theirs is measured; ours is a straight line times a fitted factor.
    billed_km = created.get("distance_km", created.get("distance"))
    try:
        billed_km = float(billed_km) if billed_km is not None else None
    except (TypeError, ValueError):
        billed_km = None
    recorded_km = billed_km or distance_km
    if cost is not None:
        delivery.quoted_cost = cost
        delivery.quoted_currency = "AED"
        delivery.quoted_distance_m = int(recorded_km * 1000)
        delivery.quoted_at = datetime.now(timezone.utc)
        delivery.cost_total = cost
        delivery.price_breakdown = {
            "source": "slider_fare",
            "vehicle": assigned,
            "distance_km": round(recorded_km, 2),
            "total": str(cost),
            "currency": "AED",
            "is_estimate": False,
            # Whether the distance beside it is Slider's measured road figure or
            # our straight line times the fitted factor. The fare is theirs
            # either way; only this number can be ours.
            "distance_is_estimated": not (billed_km or bike_km or car_km),
        }
        if assigned != vehicle:
            logger.info(
                "Slider assigned %s for %s where %s was asked for",
                assigned,
                order.order_number,
                vehicle,
            )

    delivery.provider = PROVIDER
    delivery.courier_order_id = str(delivery_id)
    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = (
        str(created.get("status") or SliderStatusEnum.SEARCHING_RIDER.value)
        .strip()
        .lower()
    )
    # Slider publishes a tracking page, which lands in the same column
    # Lalamove's share link does. Internal for now, like Lalamove's: surfacing
    # it would tell the customer who is carrying the order.
    if tracking := (created.get("tracking_url") or created.get("tracking_link")):
        delivery.share_link = str(tracking)
    # A new booking carries nobody. `clear` closes any stint left open by the
    # booking this replaces, so a re-dispatch does not leave a rider from the
    # abandoned one reading as current.
    await driver_assignment.clear(db, delivery)
    delivery.booked_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.booked_at
    delivery.last_error = None
    delivery.last_payload = created

    logger.info(
        "Slider delivery %s created for %s from %s by %s (est. AED %s)",
        delivery_id,
        order.order_number,
        pickup.reference or pickup.name,
        vehicle,
        delivery.cost_total if delivery.cost_total is not None else "-",
    )

    await db.commit()
    return delivery


async def _outstanding(db: AsyncSession, order: Order) -> Decimal:
    """What is still owed on this order.

    Borrowed wholesale from `noon_send_service`, which sums in SQL rather than
    walking `order.payments` — that relationship is not eagerly loaded, and a
    lazy load from inside async SQLAlchemy is a `MissingGreenlet` and a failed
    dispatch rather than a wrong number.
    """
    from app.services.noon_send_service import outstanding_balance

    return await outstanding_balance(db, order)


async def cancel_delivery(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """Call off the rider when the order is cancelled."""
    delivery = await get_delivery(db, order.id)
    if delivery is None or not delivery.courier_order_id:
        return delivery
    if delivery.courier_status in SLIDER_FAILED_STATUSES:
        return delivery
    if not is_enabled():
        delivery.last_error = "Slider is not configured; cancel this delivery by hand"
        return delivery

    try:
        await provider.cancel_delivery(delivery.courier_order_id)
    except SliderError as exc:
        delivery.last_error = f"Could not cancel with Slider: {exc}"
        logger.warning("Slider cancel failed for %s: %s", order.order_number, exc)
        return delivery

    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = SliderStatusEnum.CANCELLED.value
    delivery.cancelled_at = datetime.now(timezone.utc)
    delivery.status_updated_at = delivery.cancelled_at
    delivery.cancel_party = "MERCHANT"
    delivery.cancel_reason = "order_cancelled"
    delivery.last_error = None
    return delivery


# ── inbound ───────────────────────────────────────────────────────────────────

#: Slider status -> the order status it implies. Anything absent leaves the
#: order alone: a rider being assigned is still "packed and waiting".
#:
#: `return_trip_started` is here rather than left as a note on the delivery row,
#: for the reason `undelivered` is on the noon Send side: an order reading
#: `out_for_delivery` — the cake is coming — while the courier record says a
#: rider stood at the door and took it away again is two statements, only one of
#: which is true, and it is not the one on the screen.
_ORDER_STATUS_FOR: dict[str, OrderStatusEnum] = {
    SliderStatusEnum.PICKED_UP.value: OrderStatusEnum.OUT_FOR_DELIVERY,
    SliderStatusEnum.IN_TRANSIT.value: OrderStatusEnum.OUT_FOR_DELIVERY,
    SliderStatusEnum.DELIVERED.value: OrderStatusEnum.DELIVERED,
    SliderStatusEnum.RETURN_TRIP_STARTED.value: OrderStatusEnum.UNDELIVERED,
}


def _reference(payload: dict[str, Any]) -> str:
    """**Our** reference off one of their pushes.

    `order_id` is the field we filled on the way out, so it is the one that
    comes back as ours. `order_number` is *theirs* — a number they allocate —
    and reading it here matched Slider's handle against our
    `courier_reference` column, which can never hit. Their handle is looked up
    separately, against `courier_order_id`.
    """
    return str(
        payload.get("order_id")
        or payload.get("reference")
        or payload.get("order_reference")
        or ""
    ).strip()


def _status(payload: dict[str, Any]) -> str:
    return (
        str(payload.get("status") or payload.get("status_code") or "").strip().lower()
    )


def _event_id(payload: dict[str, Any]) -> str:
    """
    A stable id for an event that arrived without one.

    Slider sends no event identifier, so dedup keys on what is actually unique
    about a status change: the delivery, the status, and when it happened. A
    genuine retry reproduces all three; a real transition changes at least one.
    """
    return (
        f"slider:{_reference(payload) or payload.get('order_number')}:"
        f"{_status(payload)}:{payload.get('timestamp') or payload.get('updated_at')}"
    )


async def _delivery_for(
    db: AsyncSession, payload: dict[str, Any]
) -> OrderDelivery | None:
    """The delivery this push is about, by Slider's id or by our own reference."""
    delivery_id = payload.get("order_number")
    if delivery_id:
        found = (
            (
                await db.execute(
                    select(OrderDelivery).where(
                        OrderDelivery.courier_order_id == str(delivery_id),
                        OrderDelivery.provider == PROVIDER,
                    )
                )
            )
            .scalars()
            .first()
        )
        if found is not None:
            return found

    reference = _reference(payload)
    if not reference:
        return None

    # The short reference is what goes out now; a dispatch that could not draw a
    # free number sends the order number instead, so both are looked up.
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
    """Deduplicate and apply one status push from Slider."""
    status = _status(payload)
    if not _reference(payload) and not payload.get("order_number"):
        raise SliderError("Webhook names no delivery")

    inserted = await db.execute(
        pg_insert(WebhookEvent)
        .values(
            provider="slider",
            event_id=_event_id(payload)[:255],
            event_type=(status or "unknown")[:100],
            order_number=_reference(payload)[:30] or None,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    if inserted.rowcount == 0:
        logger.info("Duplicate Slider webhook skipped: %s", _event_id(payload))
        return {"received": True, "duplicate": True}

    delivery = await _delivery_for(db, payload)
    if delivery is None:
        # A delivery we have no record of. Acknowledged and left alone rather
        # than retried at us forever.
        return {"received": True, "event_type": status, "matched": False}

    await apply_webhook(db, payload, delivery=delivery)
    return {"received": True, "event_type": status, "matched": True}


async def apply_webhook(
    db: AsyncSession,
    payload: dict[str, Any],
    delivery: OrderDelivery,
) -> OrderDelivery:
    """
    Fold one push into the delivery record, and move the order if it should.

    Out-of-order pushes are dropped by **rank**, not by clock. Slider promises
    no ordering and the only timestamp in the payload is theirs, so comparing
    clocks would mean trusting a field to decide whether to trust the payload it
    arrived in. Their statuses are linear, so the words themselves are the
    ordering — see `SLIDER_STATUS_RANK`.
    """
    updated_at = parse_time(payload.get("timestamp") or payload.get("updated_at"))
    status = _status(payload)
    if delivery.courier_status and status:
        arriving = SLIDER_STATUS_RANK.get(status, -1)
        current = SLIDER_STATUS_RANK.get(delivery.courier_status, -1)
        if arriving < current:
            logger.info(
                "Ignoring out-of-order Slider webhook for %s (%s after %s)",
                delivery.courier_order_id,
                status or "(none)",
                delivery.courier_status,
            )
            return delivery

    delivery.last_payload = payload
    if delivery_id := payload.get("order_number"):
        delivery.courier_order_id = str(delivery_id)
    if tracking := (payload.get("tracking_url") or payload.get("tracking_link")):
        delivery.share_link = str(tracking)

    # Slider names the rider inline on every push, so unlike noon Send there is
    # no detail call to make. `record` decides whether anything actually moved.
    change = await driver_assignment.record(
        db,
        delivery,
        driver_assignment.Driver.from_slider(
            payload.get("driver_info") or payload.get("driver") or payload.get("rider")
        ),
        at=updated_at,
    )
    if change.is_new_driver:
        # Before the announcement, because the register prints a driver slip off
        # this within seconds and a slip is paper: one that goes out with the
        # ETA missing cannot be corrected.
        await driver_routing.route_now(db, delivery)
        await _announce_rider(db, delivery)

    if status and status != delivery.courier_status:
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_status = status
        moment = updated_at or datetime.now(timezone.utc)
        if status in SLIDER_FAILED_STATUSES:
            delivery.cancelled_at = moment
            delivery.cancel_reason = status
            # Nobody is bringing it. The order is not cancelled — it is paid for
            # and boxed — so this is a flag for the admin, not an ending.
            delivery.last_error = (
                f"Slider reported the delivery {status} — re-dispatch required"
            )

    if updated_at is not None:
        delivery.status_updated_at = updated_at
    elif status:
        delivery.status_updated_at = datetime.now(timezone.utc)

    await _advance_order(db, delivery, at=updated_at)
    return delivery


async def refresh(db: AsyncSession, order_id: uuid.UUID) -> OrderDelivery | None:
    """
    Pull the current state of a delivery, for when a webhook never arrived.

    Slider's statuses only reach us by push, and a push that is lost is lost —
    the same hole noon Send has, and the reason they have this too. Without a
    way to ask, a rider could deliver an order and the shop would still be
    looking at `heading_to_pickup` tomorrow. That matters more during the pilot
    than it will afterwards: one account is placing real orders on production
    specifically to find out what this integration does, and "it stopped
    updating" needs an answer better than a phone call.

    Reachable from the admin's delivery panel — see
    `POST /orders/{n}/delivery/refresh`.
    """
    delivery = await get_delivery(db, order_id)
    if delivery is None or not delivery.courier_order_id:
        return delivery
    if not is_enabled():
        delivery.last_error = "Slider is not configured"
        return delivery

    try:
        details = await provider.get_delivery(str(delivery.courier_order_id))
    except SliderError as exc:
        delivery.last_error = f"Could not read the delivery from Slider: {exc}"
        return delivery

    # Fed through the real handler rather than written beside it. Ending a
    # booking here is the order's transition, the customer's email, the refund
    # path and the cancellation reason; a second copy of that is a second thing
    # to keep in step with `VALID_TRANSITIONS`, and it would drift.
    #
    # Named for where it came from rather than borrowing a status push's shape:
    # `last_payload` is the first thing anybody reads when asking what the
    # courier said, and a fabricated payload wearing a real event's name is a
    # lie in the one place that is supposed to be evidence. No timestamp, for
    # the same reason — the rank guard drops anything older than what we hold,
    # and the genuine moment may well predate a push we did receive, so the
    # honest stamp is now.
    await apply_webhook(
        db,
        {
            "id": delivery.courier_order_id,
            "status": _status(details),
            "rider": details.get("rider") or details.get("driver"),
            "tracking_url": details.get("tracking_url"),
            "source": "slider_refresh",
        },
        delivery=delivery,
    )
    return delivery


async def _announce_rider(db: AsyncSession, delivery: OrderDelivery) -> None:
    """Same as the other two: best-effort, never fails the webhook."""
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
            db,
            order,
            rider_name=delivery.driver_name,
            rider_phone=delivery.driver_phone,
            assignment=delivery.driver_assignment_count or 1,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("Could not announce the rider for %s", order.order_number)


async def _advance_order(
    db: AsyncSession, delivery: OrderDelivery, *, at: datetime | None = None
) -> None:
    """Move the order to match the courier, stamped with the courier's clock."""
    target = _ORDER_STATUS_FOR.get(delivery.courier_status or "")
    if target is None:
        return

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

    # One rulebook, not a private copy of it: `transition` refuses stale news
    # about settled orders, refuses a parcel coming back undelivered before
    # anyone collected it, and carries the consequences — a courier marking an
    # order undelivered triggers the same automatic refund an admin's click does.
    #
    # The courier's own word goes in the note, so the history records
    # `return_trip_started` next to `undelivered` rather than only our
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
    # The customer hears about it from here. These transitions only ever happen
    # because a courier said so, so this is the only place that knows they
    # happened — and "out for delivery" is the email carrying the live tracking
    # link, which is worth nothing an hour late.
    await email_service.notify_order(db, order)
