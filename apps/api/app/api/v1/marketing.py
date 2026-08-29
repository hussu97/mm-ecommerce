"""Discounts, promotions and timed events."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    Discount,
    Promotion,
    TimedEvent,
)

from .pos_config import build_crud_router

Translations = dict[str, dict[str, str]]
OrderTypeLiteral = Literal["pickup", "delivery"]
#: `OrderSourceEnum` values — the channel that rang an order up. `cashier` is the
#: counter, the one source the POS itself creates.
SourceLiteral = Literal["cashier", "online", "aggregator", "api", "call_center"]

#: The rewards `auto_apply` may carry: both reduce to a single order-level
#: discount the pricing engine can add unattended. Mirrors
#: `auto_promotion_service._AUTO_REWARDS`.
_AUTO_APPLY_REWARDS = {"percentage_off_order", "fixed_off_order"}


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# A sixth copy of the `_require(user, permission)` helper used to sit here, and
# it was the strangest of the six: nothing in this file ever called it. Every
# route below is built by `build_crud_router`, which gates reads on
# `get_current_active_user` and writes on `get_admin_user` — so the copy was a
# permission check that looked like protection and enforced nothing.
#
# That is the failure mode `app.core.permissions` exists to end. A check written
# out imperatively per router can be forgotten (`pos_orders.add_item` shipped as
# a hole for exactly that reason) or, as here, written and never wired up, and
# neither shows up in a grep for "which routes demand what". The five live
# copies became `require(...)`/`ensure(...)`; this dead one is simply gone.
# Should these three entities ever need finer gating than "admin", they take a
# `Depends(require("marketing.<thing>"))` like everybody else.


# ─── Discounts ────────────────────────────────────────────────────────────────


class DiscountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    qualification: Literal["product", "order", "both"] = "order"
    amount: Decimal = Field(Decimal("0"), ge=0)
    is_percentage: bool = True
    is_taxable: bool = True
    minimum_order_price: Decimal = Field(Decimal("0"), ge=0)
    minimum_product_price: Decimal = Field(Decimal("0"), ge=0)
    maximum_amount: Decimal | None = Field(None, ge=0)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    is_active: bool = True


class DiscountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    qualification: Literal["product", "order", "both"] | None = None
    amount: Decimal | None = Field(None, ge=0)
    is_percentage: bool | None = None
    is_taxable: bool | None = None
    minimum_order_price: Decimal | None = Field(None, ge=0)
    minimum_product_price: Decimal | None = Field(None, ge=0)
    maximum_amount: Decimal | None = Field(None, ge=0)
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    is_active: bool | None = None


class DiscountResponse(ORMModel):
    id: uuid.UUID
    name: str
    name_localized: str | None
    reference: str | None
    qualification: str
    amount: Decimal
    is_percentage: bool
    is_taxable: bool
    minimum_order_price: Decimal
    minimum_product_price: Decimal
    maximum_amount: Decimal | None
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


discounts_router = build_crud_router(
    model=Discount,
    create_schema=DiscountCreate,
    update_schema=DiscountUpdate,
    response_schema=DiscountResponse,
    entity_type="discount",
)


# ─── Promotions ───────────────────────────────────────────────────────────────


class ScheduleFields(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    from_time: int = Field(0, ge=0, le=1439)
    to_time: int = Field(1439, ge=0, le=1439)
    is_mon: bool = True
    is_tue: bool = True
    is_wed: bool = True
    is_thu: bool = True
    is_fri: bool = True
    is_sat: bool = True
    is_sun: bool = True


class PromotionCreate(ScheduleFields):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: Literal["basic", "advanced"] = "basic"
    trigger: Literal["quantity", "spend"] = "spend"
    trigger_value: Decimal = Field(Decimal("0"), ge=0)
    reward: Literal[
        "percentage_off_products",
        "fixed_off_products",
        "percentage_off_order",
        "fixed_off_order",
        "fixed_price",
        "free_product",
    ]
    reward_value: Decimal = Field(Decimal("0"), ge=0)
    trigger_product_ids: list[uuid.UUID] = Field(default_factory=list)
    reward_product_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Categories an auto-apply order discount is confined to; empty = the whole
    #: order. Discounts only the lines whose product is in one of these categories.
    category_ids: list[uuid.UUID] = Field(default_factory=list)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    #: Channels this promotion may fire on; empty = every channel. A
    #: counter-only offer carries `["cashier"]`.
    sources: list[SourceLiteral] = Field(default_factory=list)
    customer_tag_ids: list[uuid.UUID] = Field(default_factory=list)
    priority: int = Field(100, ge=0, le=10000)
    max_uses_per_order: int = Field(1, ge=1, le=100)
    #: When true, the pricing engine applies this promotion with no cashier
    #: action. Only order-level rewards on a spend trigger can be auto-applied.
    auto_apply: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def _auto_apply_needs_order_level_reward(self) -> "PromotionCreate":
        if self.auto_apply and (
            self.reward not in _AUTO_APPLY_REWARDS or self.trigger != "spend"
        ):
            raise ValueError(
                "auto_apply is only allowed on an order-level reward "
                "(percentage_off_order or fixed_off_order) with a spend trigger"
            )
        return self


class PromotionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    type: Literal["basic", "advanced"] | None = None
    trigger: Literal["quantity", "spend"] | None = None
    trigger_value: Decimal | None = Field(None, ge=0)
    reward: str | None = None
    reward_value: Decimal | None = Field(None, ge=0)
    trigger_product_ids: list[uuid.UUID] | None = None
    reward_product_ids: list[uuid.UUID] | None = None
    category_ids: list[uuid.UUID] | None = None
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    sources: list[SourceLiteral] | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    max_uses_per_order: int | None = Field(None, ge=1, le=100)
    auto_apply: bool | None = None
    from_date: date | None = None
    to_date: date | None = None
    from_time: int | None = Field(None, ge=0, le=1439)
    to_time: int | None = Field(None, ge=0, le=1439)
    is_mon: bool | None = None
    is_tue: bool | None = None
    is_wed: bool | None = None
    is_thu: bool | None = None
    is_fri: bool | None = None
    is_sat: bool | None = None
    is_sun: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _auto_apply_needs_order_level_reward(self) -> "PromotionUpdate":
        # Best-effort on a partial update: only what this payload can see. An
        # `auto_apply` set on a promotion whose reward is product-level without
        # being touched here is still harmless — `auto_promotion_service` never
        # applies a non-order-level reward, so it simply does nothing.
        if self.auto_apply:
            if self.reward is not None and self.reward not in _AUTO_APPLY_REWARDS:
                raise ValueError(
                    "auto_apply needs an order-level reward "
                    "(percentage_off_order or fixed_off_order)"
                )
            if self.trigger is not None and self.trigger != "spend":
                raise ValueError("auto_apply needs a spend trigger")
        return self


class PromotionResponse(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    trigger: str
    trigger_value: Decimal
    reward: str
    reward_value: Decimal
    trigger_product_ids: list[uuid.UUID]
    reward_product_ids: list[uuid.UUID]
    category_ids: list[uuid.UUID]
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    sources: list[str]
    priority: int
    max_uses_per_order: int
    auto_apply: bool
    from_date: date | None
    to_date: date | None
    from_time: int
    to_time: int
    is_mon: bool
    is_tue: bool
    is_wed: bool
    is_thu: bool
    is_fri: bool
    is_sat: bool
    is_sun: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


promotions_router = build_crud_router(
    model=Promotion,
    create_schema=PromotionCreate,
    update_schema=PromotionUpdate,
    response_schema=PromotionResponse,
    entity_type="promotion",
)


# ─── Timed events ─────────────────────────────────────────────────────────────


class TimedEventCreate(ScheduleFields):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: Literal["percentage", "fixed", "fixed_price"]
    value: Decimal = Field(Decimal("0"), ge=0)
    product_ids: list[uuid.UUID] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    priority: int = Field(100, ge=0, le=10000)
    is_active: bool = True


class TimedEventUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    type: Literal["percentage", "fixed", "fixed_price"] | None = None
    value: Decimal | None = Field(None, ge=0)
    product_ids: list[uuid.UUID] | None = None
    category_ids: list[uuid.UUID] | None = None
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    from_date: date | None = None
    to_date: date | None = None
    from_time: int | None = Field(None, ge=0, le=1439)
    to_time: int | None = Field(None, ge=0, le=1439)
    is_mon: bool | None = None
    is_tue: bool | None = None
    is_wed: bool | None = None
    is_thu: bool | None = None
    is_fri: bool | None = None
    is_sat: bool | None = None
    is_sun: bool | None = None
    is_active: bool | None = None


class TimedEventResponse(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    value: Decimal
    product_ids: list[uuid.UUID]
    category_ids: list[uuid.UUID]
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    priority: int
    from_date: date | None
    to_date: date | None
    from_time: int
    to_time: int
    is_mon: bool
    is_tue: bool
    is_wed: bool
    is_thu: bool
    is_fri: bool
    is_sat: bool
    is_sun: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


timed_events_router = build_crud_router(
    model=TimedEvent,
    create_schema=TimedEventCreate,
    update_schema=TimedEventUpdate,
    response_schema=TimedEventResponse,
    entity_type="timed_event",
)


__all__ = [
    "discounts_router",
    "promotions_router",
    "timed_events_router",
]
