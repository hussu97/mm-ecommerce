"""Courses — how a menu is grouped and a kitchen sequenced."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    #: Firing order — starters before mains before dessert.
    display_order: int = 0
    is_active: bool = True


class CourseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    display_order: int | None = None
    is_active: bool | None = None


class CourseResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations = {}
    display_order: int
    is_active: bool
