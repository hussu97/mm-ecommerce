"""Sections and the tables inside them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations


class SectionCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations = Field(default_factory=dict)
    layout: dict = Field(default_factory=dict)
    display_order: int = 0
    is_active: bool = True


class SectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations | None = None
    layout: dict | None = None
    display_order: int | None = None
    is_active: bool | None = None


class TableCreate(BaseModel):
    section_id: UUID
    name: str = Field(min_length=1, max_length=60)
    seats: int = Field(4, ge=1, le=100)
    accepts_reservations: bool = True
    revenue_center_tag_id: UUID | None = None
    layout: dict = Field(default_factory=dict)
    display_order: int = 0
    is_active: bool = True


class TableUpdate(BaseModel):
    section_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=60)
    seats: int | None = Field(None, ge=1, le=100)
    status: Literal["free", "occupied", "check_printed", "reserved"] | None = None
    accepts_reservations: bool | None = None
    parent_id: UUID | None = None
    revenue_center_tag_id: UUID | None = None
    layout: dict | None = None
    display_order: int | None = None
    is_active: bool | None = None


class TableResponse(ORMModel):
    id: UUID
    section_id: UUID
    name: str
    seats: int
    status: str
    accepts_reservations: bool
    parent_id: UUID | None
    revenue_center_tag_id: UUID | None
    layout: dict
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SectionResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    layout: dict
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tables: list[TableResponse] = []
