"""Service charges added to a check."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from ._base import OrderTypeLiteral, ORMModel, Translations


class ChargeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    type: Literal["percentage", "fixed", "open"]
    value: Decimal = Field(Decimal("0"), ge=0)
    is_auto_applied: bool = False
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    tax_group_id: UUID | None = None
    is_active: bool = True

    @field_validator("value")
    @classmethod
    def _percentage_within_range(cls, v: Decimal, info) -> Decimal:
        if info.data.get("type") == "percentage" and v > 1:
            raise ValueError("percentage charges are fractions, e.g. 0.10 for 10%")
        return v


class ChargeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    type: Literal["percentage", "fixed", "open"] | None = None
    value: Decimal | None = Field(None, ge=0)
    is_auto_applied: bool | None = None
    order_types: list[OrderTypeLiteral] | None = None
    tax_group_id: UUID | None = None
    is_active: bool | None = None


class ChargeResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str | None
    type: str
    value: Decimal
    is_auto_applied: bool
    order_types: list[str]
    tax_group_id: UUID | None
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
