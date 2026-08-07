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
    #: The same coupon in Arabic, optional. A second name for one row, not a
    #: second coupon: every ceiling and every count is shared.
    #:
    #: The pattern allows Arabic letters and digits. `code`'s `^[A-Z0-9]+$`
    #: would reject every Arabic string there is.
    code_ar: str | None = Field(
        None, min_length=3, max_length=50, pattern=r"^[\u0600-\u06FF0-9\-]+$"
    )
    discount_type: DiscountTypeEnum
    discount_value: Decimal = Field(gt=0, decimal_places=2)
    min_order_amount: Decimal | None = Field(None, ge=0)
    #: The most this code can take off, in AED. Only meaningful on a percentage:
    #: 15% of a 60-dirham order is 9, and of a 600-dirham catering order is 90.
    max_discount_amount: Decimal | None = Field(None, gt=0)
    #: Require a proved phone number. Without it, a first-orders limit is
    #: enforced against identities anyone can mint for free.
    requires_phone_verification: bool = False
    #: Restricts the code to a customer's first N orders — an acquisition offer
    #: rather than a loyalty one. Counted across account, email and phone
    #: together, because guest checkout mints a fresh account per session.
    first_orders_limit: int | None = Field(None, ge=1)
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
    code_ar: str | None = Field(
        None, min_length=3, max_length=50, pattern=r"^[\u0600-\u06FF0-9\-]+$"
    )
    discount_value: Decimal | None = Field(None, gt=0)
    min_order_amount: Decimal | None = None
    max_discount_amount: Decimal | None = Field(None, gt=0)
    requires_phone_verification: bool | None = None
    first_orders_limit: int | None = Field(None, ge=1)
    max_uses: int | None = None
    max_uses_per_user: int | None = Field(None, ge=1)
    is_active: bool | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class PromoCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    code_ar: str | None = None
    discount_type: DiscountTypeEnum
    discount_value: float
    min_order_amount: float | None
    max_discount_amount: float | None = None
    requires_phone_verification: bool = False
    first_orders_limit: int | None = None
    max_uses: int | None
    max_uses_per_user: int | None
    current_uses: int
    is_active: bool
    valid_from: datetime | None
    valid_until: datetime | None
    created_at: datetime


class PromoCodeAdvertResponse(BaseModel):
    """
    The one coupon the storefront is allowed to put on a page.

    Deliberately a different shape from `PromoCodeResponse` rather than a reuse
    of it. That one is an admin's view of a campaign and carries how it is
    doing; this one is public, and answers only what a shopper needs in order to
    decide: what to type, what it takes off, the ceiling on that, and how many
    orders it covers.

    `current_uses` and `max_uses` are the fields most obviously missing, and
    their absence is the point. A public counter tells anyone watching how close
    a code is to exhaustion, which is an invitation to burn the rest of it — and
    it is a number the customer can do nothing with either way.
    """

    model_config = ConfigDict(from_attributes=True)

    code: str
    #: The same coupon spelled in Arabic, where it has one. A second name for
    #: one row: applying either resolves to the same coupon and the same limits.
    code_ar: str | None = None
    #: Carried so the storefront can tell "15% off" from "15 AED off" rather
    #: than assuming, and say nothing at all about a shape it has no copy for.
    discount_type: DiscountTypeEnum
    discount_value: float
    max_discount_amount: float | None = None
    first_orders_limit: int | None = None
    #: Whether redeeming this needs a proved phone number. Advertised because it
    #: is a condition the customer has to meet, and finding out at checkout that
    #: the offer needs something they have not done is the worst moment to learn
    #: it.
    requires_phone_verification: bool = False


class PromoCodeValidateRequest(BaseModel):
    code: str
    order_subtotal: Decimal = Field(ge=0)
    #: Who is asking, as far as the checkout knows so far. Both optional: the
    #: code is typed before the order exists and sometimes before either is
    #: filled in.
    #:
    #: Sent so the "new customers only" answer arrives while the customer can
    #: still act on it. Without them the check would first bite at submit, after
    #: the discount had already been shown as applied — and the signed-in case
    #: would be caught while the guest case, the one that matters, would not.
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=30)


class PromoCodeValidateResponse(BaseModel):
    valid: bool
    discount_amount: Decimal = Decimal("0.00")
    message: str | None = None
    #: True when the only thing standing between this customer and the discount
    #: is an unverified phone. Lets the checkout put the OTP button next to the
    #: message instead of leaving them to work it out from the wording.
    requires_phone_verification: bool = False

    @field_serializer("discount_amount")
    def _serialize_discount_amount(self, v: Decimal) -> float:
        # JSON has no Decimal type; serialize as float so API consumers
        # receive a number (e.g. 15.0) rather than a string ("15.00").
        return float(v)
