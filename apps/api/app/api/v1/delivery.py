from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_optional_user
from app.models.order import DeliveryMethodEnum
from app.models.user import User
from app.services import cart_service, delivery_service

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
    free_threshold: float
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
    elif data.subtotal >= settings.free_delivery_threshold:
        reason = f"Free delivery on orders over {settings.free_delivery_threshold} AED"
    elif fee == Decimal("0.00"):
        # Free for a reason the customer does not need explained.
        reason = "Free delivery"
    else:
        reason = None

    return DeliveryCalculateResponse(
        delivery_fee=fee, is_free=(fee == Decimal("0.00")), reason=reason
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
