from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user
from app.core.limiter import limiter
from app.models.order import DeliveryMethodEnum
from app.models.user import User
from app.services import cart_service, delivery_service, delivery_zone_service

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


class DeliveryEstimateResponse(BaseModel):
    #: ISO 8601, on the shop's clock.
    at: str
    precision: str


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
    #: How fast, as a promise rather than a mechanism: `express` is inside the
    #: hour, `same_day` means it goes out on one of today's runs, `next_day` is
    #: everywhere we hand to someone else. Three values because the answer
    #: genuinely is three, and collapsing them would make the near zones wear
    #: the far zones' promise.
    speed: str = "next_day"


class DeliveryQuoteResponse(BaseModel):
    #: Null until there is something to price — no pin yet, or a pin nothing can
    #: be delivered to. `serviceable` is what tells the two apart.
    delivery_fee: float | None = None
    base_fee: float | None = None
    free_delivery_applied: bool
    #: Whether free delivery reaches this pin at all. False in the areas priced
    #: from a live courier quote — there is no fee of ours to waive there, only
    #: a bill that arrives whatever the basket is worth.
    free_delivery_available: bool = True
    #: The basket that earns free delivery *at this pin*. Thresholds vary by
    #: zone, so this is the zone's own number wherever one has resolved.
    free_threshold: float
    #: True when the threshold above is the national default standing in for a
    #: zone we cannot resolve yet — i.e. no pin has been dropped. Copy driven by
    #: it must stay hedged until it turns false. Defaulted so an older client
    #: that does not read it behaves exactly as before.
    free_threshold_provisional: bool = False
    remaining_for_free: float
    zone_name: str | None = None
    in_known_zone: bool
    #: When the order should arrive, and how precisely. `precision` is "time"
    #: where the schedule is ours to read and "day" where it is not. Null until
    #: there is a pin to read it from.
    delivery_estimate: DeliveryEstimateResponse | None = None
    #: False when we cannot deliver to this pin at all. The checkout says so and
    #: refuses to submit; the API refuses the order too, so the two agree.
    serviceable: bool = True


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
    current_user: User | None = Depends(get_optional_user),
):
    """
    The delivery fee for a pin and an order subtotal.

    The identity is read for one reason only: a trial account pays nothing, and
    this endpoint has to agree with what the order will actually charge.
    """
    settings = await delivery_service.get_settings(db)
    fee = await delivery_service.calculate_fee(
        data.delivery_method,
        data.subtotal,
        db,
        settings=settings,
        latitude=data.latitude,
        longitude=data.longitude,
        user_id=current_user.id if current_user else None,
        email=current_user.email if current_user else None,
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
    if zone.is_noon_send:
        # A bike, dispatched on its own, inside noon Send's 20 km reach.
        return "express"
    if zone.books_itself:
        # Ours to dispatch, but batched onto a shared run — so it lands today
        # if it makes a window, and tomorrow if it misses the last one.
        return "same_day"
    return "next_day"


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
    return DeliveryAreaResponse(
        serviceable=True,
        # The emirate, not the cost band — see `public_zone_name`.
        zone_name=delivery_service.public_zone_name(zone.name),
        delivery_fee=None if zone.is_dynamic else float(zone.delivery_fee),
        free_threshold=None if threshold is None else float(threshold),
        free_delivery_available=zone.free_delivery_eligible,
        speed=_speed_of(zone),
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
    filed against, and to spot a trial account, who pays no delivery fee.
    Nothing about the courier appears in the response either way.
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
        user_id=current_user.id if current_user else None,
        email=current_user.email if current_user else None,
    )
