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
    region: str | None = None
    subtotal: Decimal
    latitude: Decimal | None = None
    longitude: Decimal | None = None


class DeliveryQuoteRequest(BaseModel):
    subtotal: Decimal
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    #: The pin's formatted address. Passed to the courier so its own estimate
    #: is taken against the same place the driver would be sent to.
    address: str | None = None


class DeliveryQuoteResponse(BaseModel):
    delivery_fee: float
    base_fee: float
    free_delivery_applied: bool
    free_threshold: float
    remaining_for_free: float
    zone_name: str | None = None
    region_slug: str | None = None
    in_known_zone: bool


class DeliveryCalculateResponse(BaseModel):
    delivery_fee: Decimal
    is_free: bool
    reason: str | None = None


@router.get("/rates", response_model=dict[str, Any])
async def get_rates(db: AsyncSession = Depends(get_db)):
    """Return all active delivery regions and the free-shipping threshold."""
    return await delivery_service.get_delivery_rates(db)


@router.post("/calculate", response_model=DeliveryCalculateResponse)
async def calculate_delivery(
    data: DeliveryCalculateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Calculate the delivery fee for a given region and order subtotal."""
    settings = await delivery_service.get_settings(db)
    fee = await delivery_service.calculate_fee(
        data.delivery_method,
        data.region,
        data.subtotal,
        db,
        settings=settings,
        latitude=data.latitude,
        longitude=data.longitude,
    )

    if data.delivery_method == DeliveryMethodEnum.PICKUP:
        reason = "Free pickup"
    elif data.subtotal >= settings.free_delivery_threshold:
        reason = f"Free delivery on orders over {settings.free_delivery_threshold} AED"
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

    The identity is only used to find the basket the courier's own estimate
    gets filed against. Nothing about the courier appears in the response.
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
