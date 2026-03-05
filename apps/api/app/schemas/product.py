from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .category import CategoryResponse


class ProductVariantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    sku: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=0, decimal_places=2)
    stock_quantity: int = Field(ge=0, default=0)
    display_order: int = 0


class ProductVariantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    sku: str | None = None
    price: Decimal | None = Field(None, gt=0)
    stock_quantity: int | None = Field(None, ge=0)
    is_active: bool | None = None
    display_order: int | None = None


class ProductVariantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    name: str
    sku: str
    price: float
    stock_quantity: int
    is_active: bool
    display_order: int


class ProductCreate(BaseModel):
    category_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    base_price: Decimal = Field(gt=0, decimal_places=2)
    image_urls: list[str] = Field(default_factory=list)
    is_featured: bool = False
    display_order: int = 0
    variants: list[ProductVariantCreate] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, pattern=r"^[a-z0-9-]+$")
    description: str | None = None
    base_price: Decimal | None = Field(None, gt=0)
    image_urls: list[str] | None = None
    is_active: bool | None = None
    is_featured: bool | None = None
    display_order: int | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID | None
    name: str
    slug: str
    description: str | None
    base_price: float
    image_urls: list[str]
    is_active: bool
    is_featured: bool
    display_order: int
    created_at: datetime
    updated_at: datetime
    variants: list[ProductVariantResponse] = []
    category: CategoryResponse | None = None
