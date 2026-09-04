from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user
from app.core.limiter import limiter
from app.models.courier import Courier, UnbatchedPromiseEnum
from app.models.order import DeliveryMethodEnum
from app.models.user import User

# Moved to `schemas` when `POST /orders/preview` started answering the same
# shape; re-exported here because this is where they have always been imported
# from. See `app/schemas/delivery.py`.
from app.schemas.delivery import (  # noqa: F401
    DeliveryEstimateResponse,
    DeliveryQuoteResponse,
)
from app.services import cart_service
from app.services.delivery import delivery_service, delivery_zone_service

router = APIRouter()


class DeliveryCalculateRequest(BaseModel):
    delivery_method: DeliveryMethodEnum
    subtotal: Decimal
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class DeliveryQuoteRequest(BaseModel):
    subtotal: Decimal
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    #: The pin's formatted address. Passed to the courier so its own estimate
    #: is taken against the same place the driver would be sent to — and, where
    #: the fee is that estimate, against the place it is charged for.
    address: str | None = None


class DeliveryAreaResponse(BaseModel):
    """
    What delivery looks like at a pin, before there is a basket.

    Separate from `/quote` because the questions are different. A quote needs a
    cart, a subtotal and a live courier estimate, and it exists to put a number
    on one order. This answers "what is delivery like where I live" for a banner
    on the homepage and a line on a product card — pages a customer reaches long
    before a cart exists.

    Cheap on purpose: a point-in-polygon lookup against the cached map and
    nothing else. No courier is called, so it costs no money and adds no latency
    to a page that is mostly images.
    """

    serviceable: bool = True
    #: The place, never the carrier. Same rule as the quote: `zone_name` reaches
    #: the browser, so it may only ever name somewhere.
    zone_name: str | None = None
    #: What this zone charges, before any basket. Null where the fee is a live
    #: courier quote and cannot be known without one.
    delivery_fee: float | None = None
    #: The basket that earns free delivery *here*.
    free_threshold: float | None = None
    free_delivery_available: bool = False
    #: How fast, as a promise rather than a mechanism: `express` is a zone we
    #: dispatch ourselves (a rider called the moment the box is packed) and
    #: `next_day` is everywhere we hand to someone else who collects on their own
    #: schedule.
    speed: str = "next_day"
    #: The minutes an `express` badge says out loud, e.g. `90` — read from the
    #: same courier row the checkout's `delivery_promise` quotes, so the product
    #: card and the checkout cannot name two different durations for one pin. It
    #: is the number and never the courier: a duration leaks nothing a fee does
    #: not. Null for `next_day` (nothing to count in minutes), and
    #: null for an express zone whose courier promises a day rather than minutes,
    #: where the card keeps its own wording rather than inventing a figure.
    express_minutes: int | None = None
    #: The kitchen this pin resolves to, so the storefront can ask the catalogue
    #: what that kitchen has. Resolved exactly as an order would resolve it, and
    #: that is the point of sending it: the shopper is shown what the checkout
    #: will accept rather than what the estate can collectively make.
    #:
    #: An id and not a name. It is a key the browser hands back on catalogue
    #: reads, and naming a branch to a customer is a different decision —
    #: `zone_name` is the one thing here that may name a place.
    #:
    #: Null for a pin nowhere near us, where there is no kitchen to answer for
    #: and no order to place either.
    branch_id: uuid.UUID | None = None


class DeliveryCalculateResponse(BaseModel):
    delivery_fee: Decimal
    is_free: bool
    reason: str | None = None


@router.get("/rates", response_model=dict[str, Any])
async def get_rates(db: AsyncSession = Depends(get_db)):
    """The free-delivery threshold, the pickup fee and the outside-every-zone fee."""
    return await delivery_service.get_delivery_rates(db)


@router.post("/calculate", response_model=DeliveryCalculateResponse)
async def calculate_delivery(
    data: DeliveryCalculateRequest,
    db: AsyncSession = Depends(get_db),
):
    """The delivery fee for a pin and an order subtotal."""
    settings = await delivery_service.get_settings(db)
    fee = await delivery_service.calculate_fee(
        data.delivery_method,
        data.subtotal,
        db,
        settings=settings,
        latitude=data.latitude,
        longitude=data.longitude,
    )

    if data.delivery_method == DeliveryMethodEnum.PICKUP:
        reason = "Free pickup"
    elif fee == Decimal("0.00"):
        # Free for a reason the customer does not need explained.
        reason = "Free delivery"
    else:
        reason = None

    return DeliveryCalculateResponse(
        delivery_fee=fee, is_free=(fee == Decimal("0.00")), reason=reason
    )


#: Zone shape -> the promise we make about it. Keyed on what the zone *is*
#: rather than on its name, so redrawing the map cannot silently change what a
#: customer was told.
def _speed_of(zone) -> str:
    if zone is None:
        return "next_day"
    if zone.books_itself:
        # Ours to dispatch and waiting for nobody: a rider is called the moment
        # the box is packed. Every self-booked zone dispatches directly — there
        # is no shared run to wait for.
        return "express"
    return "next_day"


#: The number behind an `express` badge, read off the same courier row the
#: checkout quotes. Kept beside `_speed_of` because the two answer one question
#: together — how fast, and how fast in minutes — and only ever disagreed
#: because the card had no way to read the second and hardcoded an hour.
async def _express_minutes(db: AsyncSession, zone, speed: str) -> int | None:
    # Only `express` counts in minutes; the other two promise a day, and a
    # number there would be a precision we do not have.
    if speed != "express":
        return None
    courier = (
        await db.execute(
            select(Courier).where(Courier.code == zone.fulfilment_provider)
        )
    ).scalar_one_or_none()
    # No courier row, or one that promises a day rather than an hour: null, and
    # the card falls back to its own wording rather than a figure nothing set.
    if (
        courier is None
        or courier.unbatched_promise_kind != UnbatchedPromiseEnum.MINUTES.value
    ):
        return None
    return courier.unbatched_promise_minutes


@router.get("/area", response_model=DeliveryAreaResponse)
@limiter.limit("60/minute")
async def delivery_area(
    request: Request,
    # Bounded, so the only things that reach the point-in-polygon lookup are
    # points. A bare `float` accepts `nan` and `inf` — Python parses both — and
    # a NaN compares false against every bound in every shape, which is a
    # coordinate that silently answers "nowhere we deliver" rather than "that is
    # not a coordinate".
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    db: AsyncSession = Depends(get_db),
):
    """
    What delivery looks like at this pin. No cart, no courier call, no cost.

    Rate limited because it is public and unauthenticated, and because a pin
    plus a zone name is a slow way to trace the delivery map. Sixty a minute is
    far above what a browsing customer generates and far below what mapping the
    country would need.
    """
    zone = await delivery_zone_service.find_zone(db, latitude, longitude)

    if zone is None:
        # Outside every shape on the active map, which tiles the whole country —
        # so this is outside the UAE, not an address we have not drawn yet.
        # `serviceable=False` here matches what `/quote` and order creation now
        # do, so the three cannot disagree about the same pin.
        return DeliveryAreaResponse(
            serviceable=False,
            zone_name=None,
            delivery_fee=None,
            free_threshold=None,
            free_delivery_available=False,
            speed="next_day",
        )

    threshold = zone.free_delivery_threshold
    speed = _speed_of(zone)
    return DeliveryAreaResponse(
        serviceable=True,
        # The emirate, not the cost band — see `public_zone_name`.
        zone_name=delivery_service.public_zone_name(zone.name),
        delivery_fee=None if zone.is_dynamic else float(zone.delivery_fee),
        free_threshold=None if threshold is None else float(threshold),
        free_delivery_available=zone.free_delivery_eligible,
        speed=speed,
        express_minutes=await _express_minutes(db, zone, speed),
        # The zone's own kitchen, and deliberately not `order_service.
        # resolve_branch`. That function must always name one, because an order
        # has to be baked somewhere, so it ends at "any active branch" — a guess
        # that is right for writing a row and wrong for filtering a catalogue.
        # Null here means we do not know which kitchen, and the storefront's
        # answer to not knowing is to show what any branch can make, which is
        # the wider and safer of the two.
        #
        # It is also the whole cost of this field: one column already loaded,
        # on an endpoint the browser calls every time the pin moves.
        branch_id=zone.branch_id,
    )


@router.post("/quote", response_model=DeliveryQuoteResponse)
async def quote_delivery(
    data: DeliveryQuoteRequest,
    x_session_id: str | None = Header(None, alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    What delivery costs to a specific point, and how far the basket is from
    free. The checkout calls this whenever the pin or the basket changes, so the
    figure on screen is the one the order will be written with.

    The identity is used to find the basket the courier's own estimate gets
    filed against. Nothing about the courier appears in the response either way.
    """
    cart = await cart_service.find_cart(
        db,
        current_user.id if current_user else None,
        None if current_user else x_session_id,
    )
    return await delivery_service.quote(
        db,
        data.subtotal,
        latitude=data.latitude,
        longitude=data.longitude,
        cart=cart,
        address=data.address,
    )
