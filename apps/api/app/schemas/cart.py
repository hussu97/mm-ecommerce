from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SelectedOption(BaseModel):
    modifier_id: UUID
    option_id: UUID


class CartItemCreate(BaseModel):
    product_id: UUID
    quantity: int = Field(ge=1, le=99, default=1)
    selected_options: list[SelectedOption] = Field(default_factory=list)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    product_id: UUID
    quantity: int
    selected_options: list[Any] = []
    created_at: datetime

    # Computed fields (populated by service layer)
    product_name: str | None = None
    product_image: str | None = None
    unit_price: float | None = None
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
