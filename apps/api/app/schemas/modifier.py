from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModifierOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    modifier_id: UUID
    name: str
    translations: dict[str, dict[str, str]] = {}
    sku: str
    price: float
    #: The marketplace price when it differs from ``price``; null otherwise.
    aggregator_price: float | None = None
    calories: int | None
    is_active: bool
    display_order: int


class ModifierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reference: str
    name: str
    translations: dict[str, dict[str, str]] = {}
    is_active: bool
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
    translations: dict[str, dict[str, str]] = {}


class ModifierUpdate(BaseModel):
    name: str | None = None
    translations: dict[str, dict[str, str]] = {}
    is_active: bool | None = None


class ModifierOptionCreate(BaseModel):
    name: str
    translations: dict[str, dict[str, str]] = {}
    sku: str
    price: Decimal = Decimal("0")
    #: What the marketplaces charge instead of ``price``; null = the same.
    aggregator_price: Decimal | None = Field(None, ge=0, decimal_places=2)
    calories: int | None = None
    is_active: bool = True
    display_order: int = 0


class ModifierOptionUpdate(BaseModel):
    name: str | None = None
    translations: dict[str, dict[str, str]] = {}
    sku: str | None = None
    price: Decimal | None = None
    #: Sending null clears it (back to the counter price).
    aggregator_price: Decimal | None = Field(None, ge=0, decimal_places=2)
    calories: int | None = None
    is_active: bool | None = None
    display_order: int | None = None
