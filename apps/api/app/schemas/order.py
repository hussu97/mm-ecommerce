from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from .address import AddressCreate


class PaymentMethodEnum(str, enum.Enum):
    STRIPE = "stripe"
    COD = "cod"
    TABBY = "tabby"
    TAMARA = "tamara"


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID | None
    product_name: str
    product_sku: str
    product_translations: dict[str, dict[str, str]] = {}
    quantity: int
    base_price: float
    options_price: float
    unit_price: float
    total_price: float
    selected_options_snapshot: list[Any] = []


class OrderCreate(BaseModel):
    # Optional: a phone number is enough to run a delivery. Email is only
    # needed if the customer wants the confirmation in writing, so asking for
    # it as a hard requirement cost more orders than it saved.
    email: EmailStr | None = None
    delivery_method: DeliveryMethodEnum
    shipping_address: AddressCreate | None = (
        None  # required if delivery_method == delivery
    )
    promo_code: str | None = None
    payment_method: PaymentMethodEnum = Field(
        description="stripe | cod | tabby | tamara"
    )
    notes: str | None = None
    # Guest checkout: identify which cart to convert
    session_id: str | None = None


class OrderStatusUpdate(BaseModel):
    status: OrderStatusEnum
    admin_notes: str | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    user_id: UUID | None
    email: str
    delivery_method: DeliveryMethodEnum
    delivery_fee: float
    subtotal: float
    discount_amount: float
    total: float
    status: OrderStatusEnum
    promo_code_used: str | None
    shipping_address_snapshot: dict[str, Any] | None
    payment_method: str | None
    payment_provider: str | None
    payment_id: str | None
    vat_rate: float
    vat_amount: float
    total_excl_vat: float
    notes: str | None
    admin_notes: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse] = []
    #: Whether this order's email already belongs to a real account.
    #:
    #: Only ever true for a signed-up user — a guest's generated `@guest.local`
    #: identity is not an account anyone can sign in to. It rides on the order
    #: rather than living behind an "is this email registered?" endpoint,
    #: because such an endpoint answers that question for *any* address; this
    #: one only answers it for someone who already holds the order number and
    #: the email that placed it.
    email_has_account: bool = False


class OrderListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    email: str
    status: OrderStatusEnum
    total: float
    delivery_method: DeliveryMethodEnum
    payment_provider: str | None
    created_at: datetime
    item_count: int = 0
