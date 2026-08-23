"""The reason codes a void or a return must cite."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations

ReasonTypeLiteral = Literal["void_return", "quantity_adjustment", "drawer_operation"]


class ReasonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: ReasonTypeLiteral
    is_active: bool = True


class ReasonUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations | None = None
    type: ReasonTypeLiteral | None = None
    is_active: bool | None = None


class ReasonResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    type: str
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
