from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ModifierOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    modifier_id: UUID
    name: str
    name_localized: str | None
    sku: str
    price: float
    calories: int | None
    is_active: bool
    display_order: int


class ModifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference: str
    name: str
    name_localized: str | None
    options: list[ModifierOptionResponse] = []


class ProductModifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    modifier_id: UUID
    modifier: ModifierResponse
    minimum_options: int
    maximum_options: int
    free_options: int
    unique_options: bool
    display_order: int


class ModifierCreate(BaseModel):
    reference: str
    name: str
    name_localized: str | None = None


class ModifierUpdate(BaseModel):
    name: str | None = None
    name_localized: str | None = None


class ModifierOptionCreate(BaseModel):
    name: str
    name_localized: str | None = None
    sku: str
    price: Decimal = Decimal("0")
    calories: int | None = None
    is_active: bool = True
    display_order: int = 0
