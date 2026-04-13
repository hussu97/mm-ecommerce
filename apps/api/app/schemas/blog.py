from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BlogPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    is_active: bool
    content: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BlogPostPublicResponse(BaseModel):
    slug: str
    content: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BlogPostListPublicResponse(BaseModel):
    items: list[BlogPostPublicResponse]
    total: int
    page: int
    per_page: int
    pages: int


class BlogPostCreate(BaseModel):
    slug: str
    is_active: bool = False
    content: dict[str, Any]


class BlogPostUpdate(BaseModel):
    is_active: bool | None = None
    content: dict[str, Any] | None = None


class BlogPostLocaleUpdate(BaseModel):
    content: dict[str, Any]
