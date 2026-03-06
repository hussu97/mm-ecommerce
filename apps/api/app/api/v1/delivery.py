from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.models.address import EmirateEnum
from app.models.order import DeliveryMethodEnum
from app.services import delivery_service

router = APIRouter()


class DeliveryCalculateRequest(BaseModel):
    delivery_method: DeliveryMethodEnum
    emirate: EmirateEnum | None = None
    subtotal: Decimal


class DeliveryCalculateResponse(BaseModel):
    delivery_fee: Decimal
    is_free: bool
    reason: str | None = None


@router.get("/rates", response_model=dict[str, Any])
async def get_rates():
    """Return all delivery rate tiers and the free-shipping threshold."""
    return delivery_service.DELIVERY_RATES


@router.post("/calculate", response_model=DeliveryCalculateResponse)
async def calculate_delivery(data: DeliveryCalculateRequest):
    """Calculate the delivery fee for a given emirate and order subtotal."""
    fee = delivery_service.calculate_fee(
        data.delivery_method, data.emirate, data.subtotal
    )

    if data.delivery_method == DeliveryMethodEnum.PICKUP:
        reason = "Free pickup"
    elif data.subtotal >= delivery_service.FREE_THRESHOLD:
        reason = f"Free delivery on orders over {delivery_service.FREE_THRESHOLD} AED"
    else:
        reason = None

    return DeliveryCalculateResponse(
        delivery_fee=fee, is_free=(fee == Decimal("0.00")), reason=reason
    )
