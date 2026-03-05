from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .product import ProductVariantResponse


class CartItemCreate(BaseModel):
    variant_id: UUID
    quantity: int = Field(ge=1, le=99, default=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


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
    line_total: float | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    session_id: str | None
    items: list[CartItemResponse] = []

    # Computed fields
    item_count: int = 0
    subtotal: float = 0.0
