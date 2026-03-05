from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from .address import AddressCreate


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    variant_id: UUID | None
    product_name: str
    variant_name: str
    quantity: int
    unit_price: float
    total_price: float


class OrderCreate(BaseModel):
    email: EmailStr
    delivery_method: DeliveryMethodEnum
    shipping_address: AddressCreate | None = (
        None  # required if delivery_method == delivery
    )
    promo_code: str | None = None
    payment_method: str = Field(description="stripe | tabby | tamara")
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
    notes: str | None
    admin_notes: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse] = []


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
