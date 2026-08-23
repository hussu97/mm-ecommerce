"""Taxes and tax groups."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations


class TaxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    rate: Decimal = Field(ge=0, le=1, description="Fraction, e.g. 0.05 for 5%")
    type: Literal["inclusive", "exclusive"] = "inclusive"
    is_active: bool = True


class TaxUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    rate: Decimal | None = Field(None, ge=0, le=1)
    type: Literal["inclusive", "exclusive"] | None = None
    is_active: bool | None = None


class TaxResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    rate: Decimal
    type: str
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaxGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    tax_ids: list[UUID] = Field(default_factory=list)
    is_active: bool = True
    is_default: bool = False


class TaxGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    tax_ids: list[UUID] | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class TaxGroupResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str | None
    is_active: bool
    is_default: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    taxes: list[TaxResponse] = []
    combined_rate: Decimal = Decimal("0")
