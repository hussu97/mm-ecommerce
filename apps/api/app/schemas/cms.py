from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CmsPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    is_active: bool
    content: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CmsPagePublicResponse(BaseModel):
    slug: str
    content: dict[str, Any]


class CmsPageUpdate(BaseModel):
    content: dict[str, Any]


class CmsPageLocaleUpdate(BaseModel):
    content: dict[str, Any]
