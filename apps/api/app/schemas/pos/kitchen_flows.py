"""Kitchen flows: which categories print where."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import OrderTypeLiteral, ORMModel, Translations


class KitchenFlowCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations = Field(default_factory=dict)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    category_ids: list[UUID] = Field(default_factory=list)
    is_default: bool = False
    auto_complete_seconds: int = Field(0, ge=0, le=86400)
    display_order: int = 0
    is_active: bool = True


class KitchenFlowUpdate(BaseModel):
    branch_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations | None = None
    order_types: list[OrderTypeLiteral] | None = None
    category_ids: list[UUID] | None = None
    is_default: bool | None = None
    auto_complete_seconds: int | None = Field(None, ge=0, le=86400)
    display_order: int | None = None
    is_active: bool | None = None


class KitchenFlowResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    order_types: list[str]
    category_ids: list[UUID] = []
    is_default: bool
    auto_complete_seconds: int
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
