from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models.order import DeliveryMethodEnum
from app.services import delivery_service

router = APIRouter()


class DeliveryCalculateRequest(BaseModel):
    delivery_method: DeliveryMethodEnum
    region: str | None = None
    subtotal: Decimal


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
    fee = await delivery_service.calculate_fee(
        data.delivery_method, data.region, data.subtotal, db
    )

    settings = await delivery_service.get_settings(db)

    if data.delivery_method == DeliveryMethodEnum.PICKUP:
        reason = "Free pickup"
    elif data.subtotal >= settings.free_delivery_threshold:
        reason = f"Free delivery on orders over {settings.free_delivery_threshold} AED"
    else:
        reason = None

    return DeliveryCalculateResponse(
        delivery_fee=fee, is_free=(fee == Decimal("0.00")), reason=reason
    )
