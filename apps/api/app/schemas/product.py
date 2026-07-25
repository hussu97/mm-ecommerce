from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .category import CategoryResponse
from .modifier import ProductModifierResponse


class ProductModifierLink(BaseModel):
    modifier_id: UUID
    minimum_options: int = 0
    maximum_options: int = 1
    free_options: int = 0
    unique_options: bool = False


class ProductCreate(BaseModel):
    category_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    sku: str | None = None
    description: str | None = None
    translations: dict[str, dict[str, str]] = Field(default_factory=dict)
    base_price: Decimal = Field(ge=0, decimal_places=2, default=Decimal("0"))
    calories: int | None = None
    preparation_time: int | None = None
    is_sold_by_weight: bool = False
    #: "fixed" sells at base_price; "open" makes the cashier key the price in.
    pricing_method: Literal["fixed", "open"] = "fixed"
    is_stock_product: bool = False
    stock_quantity: int = Field(default=0, ge=0)
    image_urls: list[str] = Field(default_factory=list)
    is_featured: bool = False
    #: Listed on the public website. POS-only items are false.
    is_web_visible: bool = True
    #: Free-form nutrition panel: protein, carbs, fat, salt, allergens.
    nutrition: dict | None = None
    display_order: int = 0


class ProductUpdate(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, pattern=r"^[a-z0-9-]+$")
    sku: str | None = None
    description: str | None = None
    translations: dict[str, dict[str, str]] | None = None
    base_price: Decimal | None = Field(None, ge=0)
    calories: int | None = None
    preparation_time: int | None = None
    is_sold_by_weight: bool | None = None
    pricing_method: Literal["fixed", "open"] | None = None
    is_stock_product: bool | None = None
    stock_quantity: int | None = Field(None, ge=0)
    image_urls: list[str] | None = None
    is_active: bool | None = None
    is_featured: bool | None = None
    is_web_visible: bool | None = None
    #: Free-form nutrition panel: protein, carbs, fat, salt, allergens.
    nutrition: dict | None = None
    display_order: int | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID | None
    name: str
    slug: str
    sku: str | None
    description: str | None
    translations: dict[str, dict[str, str]] = {}
    base_price: float
    calories: int | None
    preparation_time: int | None
    is_sold_by_weight: bool
    # The terminal needs this to know whether to prompt for a price. Without
    # it an open-price product looks like an ordinary one, the cashier adds it
    # with no amount, and the order engine rejects the line with no way for
    # them to satisfy it.
    pricing_method: str = "fixed"
    is_stock_product: bool
    stock_quantity: int
    image_urls: list[str]
    is_active: bool
    is_featured: bool
    is_web_visible: bool = True
    nutrition: dict | None = None
    display_order: int
    created_at: datetime
    updated_at: datetime
    product_modifiers: list[ProductModifierResponse] = []
    category: CategoryResponse | None = None

    @property
    def has_modifiers(self) -> bool:
        return len(self.product_modifiers) > 0
