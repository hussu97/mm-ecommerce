from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.promo_code import DiscountTypeEnum


class PromoCodeCreate(BaseModel):
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9]+$")
    discount_type: DiscountTypeEnum
    discount_value: Decimal = Field(gt=0, decimal_places=2)
    min_order_amount: Decimal | None = Field(None, ge=0)
    max_uses: int | None = Field(None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class PromoCodeUpdate(BaseModel):
    discount_value: Decimal | None = Field(None, gt=0)
    min_order_amount: Decimal | None = None
    max_uses: int | None = None
    is_active: bool | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class PromoCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    discount_type: DiscountTypeEnum
    discount_value: float
    min_order_amount: float | None
    max_uses: int | None
    current_uses: int
    is_active: bool
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime


class PromoCodeValidateRequest(BaseModel):
    code: str
    order_subtotal: Decimal = Field(ge=0)


class PromoCodeValidateResponse(BaseModel):
    valid: bool
    discount_amount: Decimal = Decimal("0.00")
    message: str | None = None
