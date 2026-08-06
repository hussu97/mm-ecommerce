from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.models.promo_code import DiscountTypeEnum

#: A percentage discount above this is not a discount, it is the shop paying the
#: customer: it drives the subtotal negative, then VAT negative, and
#: `payment_service.create_session` reads the resulting total as "zero, nothing
#: to charge" and confirms the order for free. Only `gt=0` was ever enforced.
MAX_PERCENTAGE_DISCOUNT = Decimal("100")


def _check_percentage_ceiling(
    discount_type: DiscountTypeEnum | None, discount_value: Decimal | None
) -> None:
    if discount_type != DiscountTypeEnum.PERCENTAGE or discount_value is None:
        return
    if discount_value > MAX_PERCENTAGE_DISCOUNT:
        raise ValueError(
            f"A percentage discount cannot exceed {MAX_PERCENTAGE_DISCOUNT}%"
        )


class PromoCodeCreate(BaseModel):
    code: str = Field(min_length=3, max_length=50, pattern=r"^[A-Z0-9]+$")
    discount_type: DiscountTypeEnum
    discount_value: Decimal = Field(gt=0, decimal_places=2)
    min_order_amount: Decimal | None = Field(None, ge=0)
    max_uses: int | None = Field(None, ge=1)
    #: How many times one customer may redeem this code. Without it a single
    #: person could burn an entire campaign's `max_uses` alone.
    max_uses_per_user: int | None = Field(None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def _cap_percentage(self):
        _check_percentage_ceiling(self.discount_type, self.discount_value)
        return self


class PromoCodeBulkCreate(BaseModel):
    """
    Generate a batch of one-per-customer coupons.

    Foodics calls this bulk coupon creation: a campaign hands out 500 unique
    codes rather than one shared code, so a single leak cannot be redeemed by
    everyone.
    """

    #: Prepended to every generated code, e.g. "EID" -> "EID-7QK4M2".
    prefix: str = Field(min_length=1, max_length=12, pattern=r"^[A-Z0-9]+$")
    count: int = Field(ge=1, le=1000)
    discount_type: DiscountTypeEnum
    discount_value: Decimal = Field(gt=0, decimal_places=2)
    min_order_amount: Decimal | None = Field(None, ge=0)
    #: Defaults to single-use, which is the point of issuing unique codes.
    max_uses: int | None = Field(1, ge=1)
    max_uses_per_user: int | None = Field(None, ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def _cap_percentage(self):
        _check_percentage_ceiling(self.discount_type, self.discount_value)
        return self


class PromoCodeBulkResponse(BaseModel):
    created: int
    codes: list[str]


class PromoCodeUpdate(BaseModel):
    discount_value: Decimal | None = Field(None, gt=0)
    min_order_amount: Decimal | None = None
    max_uses: int | None = None
    max_uses_per_user: int | None = Field(None, ge=1)
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
    max_uses_per_user: int | None
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

    @field_serializer("discount_amount")
    def _serialize_discount_amount(self, v: Decimal) -> float:
        # JSON has no Decimal type; serialize as float so API consumers
        # receive a number (e.g. 15.0) rather than a string ("15.00").
        return float(v)
