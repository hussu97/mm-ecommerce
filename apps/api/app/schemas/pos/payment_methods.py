"""Payment methods offered at the register."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel, Translations


class PaymentMethodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    code: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    type: Literal["cash", "card", "online", "other"]
    auto_open_drawer: bool = False
    allows_tendering: bool = False
    allows_tips: bool = False
    allows_refund: bool = True
    rounding_step: str | None = Field(None, max_length=10)
    is_active: bool = True
    display_order: int = 0


class PaymentMethodUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    code: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    type: Literal["cash", "card", "online", "other"] | None = None
    auto_open_drawer: bool | None = None
    allows_tendering: bool | None = None
    allows_tips: bool | None = None
    allows_refund: bool | None = None
    rounding_step: str | None = Field(None, max_length=10)
    is_active: bool | None = None
    display_order: int | None = None


class PaymentMethodResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    code: str
    type: str
    auto_open_drawer: bool
    allows_tendering: bool
    allows_tips: bool
    allows_refund: bool
    rounding_step: str | None
    is_active: bool
    display_order: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
