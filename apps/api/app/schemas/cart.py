from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .product import ProductVariantResponse, ProductResponse


class CartItemCreate(BaseModel):
    variant_id: UUID
    quantity: int = Field(ge=1, default=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    variant_id: UUID
    quantity: int
    created_at: datetime
    variant: ProductVariantResponse | None = None

    # Computed fields (populated by service layer)
    product_name: str | None = None
    product_image: str | None = None
    line_total: Decimal | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    session_id: str | None
    items: list[CartItemResponse] = []

    # Computed fields
    item_count: int = 0
    subtotal: Decimal = Decimal("0.00")
