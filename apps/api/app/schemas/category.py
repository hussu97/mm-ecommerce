from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = None
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    reference: str | None = None
    description: str | None = None
    image_url: str | None = None
    display_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = None
    slug: str | None = Field(None, pattern=r"^[a-z0-9-]+$")
    reference: str | None = None
    description: str | None = None
    image_url: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_localized: str | None
    slug: str
    reference: str | None
    description: str | None
    image_url: str | None
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    product_count: int = 0  # computed field, not from ORM directly
