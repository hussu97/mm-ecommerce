"""Request and response models for discounts, promotions and timed events.

Moved out of `app/api/v1/marketing.py` (CLAUDE.md rule 11); the router still
re-exports them for older imports.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Translations = dict[str, dict[str, str]]
OrderTypeLiteral = Literal["pickup", "delivery"]
#: `OrderSourceEnum` values — the channel that rang an order up. `cashier` is the
#: counter, the one source the POS itself creates.
SourceLiteral = Literal["cashier", "online", "aggregator"]

#: The rewards `auto_apply` may carry: both reduce to a single order-level
#: discount the pricing engine can add unattended. Mirrors
#: `auto_promotion_service._AUTO_REWARDS`.
_AUTO_APPLY_REWARDS = {"percentage_off_order", "fixed_off_order"}


def check_branch_modes(
    *,
    auto_branch_ids: list[uuid.UUID] | None,
    coupon_branch_ids: list[uuid.UUID] | None,
    reward: str | None,
    trigger: str | None,
    sources: list[str] | None,
) -> None:
    """The rules a per-branch mode list must satisfy. Raises `ValueError`.

    Called with whatever is known: the whole payload on create, the payload on
    a partial update (a None means "not in this payload" and is not checked),
    and the merged row-plus-payload by the write path in `marketing.py`.

    * A branch runs a promotion one way — auto *or* coupon — never both.
    * Only an order-level reward on a spend trigger can run at the counter by
      mode: those are the shapes the engine turns into a discount unattended.
    * A promotion running anywhere by mode must name its channels; an unscoped
      one would discount the website and every marketplace too.
    """
    auto = set(auto_branch_ids or [])
    coupon = set(coupon_branch_ids or [])
    overlap = auto & coupon
    if overlap:
        raise ValueError(
            "A branch cannot run a promotion both automatically and as a "
            f"coupon: {sorted(str(b) for b in overlap)}"
        )
    if not (auto or coupon):
        return
    if reward is not None and reward not in _AUTO_APPLY_REWARDS:
        raise ValueError(
            "Branch modes (auto/coupon) are only allowed on an order-level "
            "reward (percentage_off_order or fixed_off_order)"
        )
    if trigger is not None and trigger != "spend":
        raise ValueError("Branch modes (auto/coupon) need a spend trigger")
    if sources is not None and not sources:
        raise ValueError(
            "Branch modes (auto/coupon) need an explicit sources scope "
            "(e.g. ['cashier']) — an unscoped promotion would discount every "
            "channel"
        )


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


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
    priority: int = Field(100, ge=0, le=10000)
    max_uses_per_order: int = Field(1, ge=1, le=100)
    #: How many completed orders may use it in total, across branches. Null =
    #: unlimited.
    usage_limit: int | None = Field(None, ge=1)
    #: Compatibility flag. The write path stores `bool(auto_branch_ids)`
    #: whatever is sent; name the branches in `auto_branch_ids` instead.
    auto_apply: bool = False
    #: Branches where the register applies it by itself. Empty = nowhere.
    auto_branch_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Branches where it is a one-tap coupon at the till. Empty = nowhere.
    #: Disjoint from `auto_branch_ids`.
    coupon_branch_ids: list[uuid.UUID] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def _branch_modes_are_coherent(self) -> "PromotionCreate":
        check_branch_modes(
            auto_branch_ids=self.auto_branch_ids,
            coupon_branch_ids=self.coupon_branch_ids,
            reward=self.reward,
            trigger=self.trigger,
            sources=self.sources,
        )
        return self

    @model_validator(mode="after")
    def _auto_apply_needs_order_level_reward(self) -> "PromotionCreate":
        if self.auto_apply and (
            self.reward not in _AUTO_APPLY_REWARDS or self.trigger != "spend"
        ):
            raise ValueError(
                "auto_apply is only allowed on an order-level reward "
                "(percentage_off_order or fixed_off_order) with a spend trigger"
            )
        # An auto-apply promotion with no `sources` is scoped to every channel,
        # so the standing "15% off counter orders" would silently discount the
        # website and every marketplace too. Auto-apply is a discount the engine
        # puts on unattended, so it MUST name the channels it fires on — a scope
        # by data, not by hope. An ordinary (cashier-invoked) promotion may still
        # leave `sources` empty; only the unattended ones are pinned down.
        if self.auto_apply and not self.sources:
            raise ValueError(
                "auto_apply needs an explicit sources scope (e.g. ['cashier']) — "
                "an unscoped auto-apply promotion would discount every channel"
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
    #: Send `null` to make it unlimited again; leave it out to keep it.
    usage_limit: int | None = Field(None, ge=1)
    #: Compatibility flag: `false` turns the promotion off at every auto
    #: branch; `true` needs `auto_branch_ids`. Stored as `bool(auto_branch_ids)`.
    auto_apply: bool | None = None
    auto_branch_ids: list[uuid.UUID] | None = None
    coupon_branch_ids: list[uuid.UUID] | None = None
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
    def _branch_modes_are_coherent(self) -> "PromotionUpdate":
        # Best-effort on a partial update: only what this payload can see. The
        # merged row is checked again by the write path before it is stored.
        check_branch_modes(
            auto_branch_ids=self.auto_branch_ids,
            coupon_branch_ids=self.coupon_branch_ids,
            reward=self.reward,
            trigger=self.trigger,
            sources=self.sources,
        )
        return self

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
            # If this payload turns auto_apply on and also names the sources, an
            # empty list is refused — same rule as create. A payload that sets
            # auto_apply without touching sources is left to `_candidates`, which
            # skips an unscoped auto promotion at apply time.
            if self.sources is not None and not self.sources:
                raise ValueError(
                    "auto_apply needs an explicit sources scope (e.g. ['cashier'])"
                )
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
    usage_limit: int | None = None
    auto_apply: bool
    auto_branch_ids: list[uuid.UUID] = Field(default_factory=list)
    coupon_branch_ids: list[uuid.UUID] = Field(default_factory=list)
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


class PromotionUsageResponse(BaseModel):
    """How much of a promotion's usage limit completed orders have used."""

    promotion_id: uuid.UUID
    #: Completed orders that carried it, across every branch.
    used: int
    #: The limit; null = unlimited.
    usage_limit: int | None
    #: `used >= usage_limit`: it is no longer offered anywhere.
    exhausted: bool


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


# ─── Counter promotions at the till ──────────────────────────────────────────


class AvailablePromotionResponse(BaseModel):
    """A counter promotion as the register lists it for one branch.

    `mode` is how it runs there: `coupon` ones are the chips the cashier taps,
    the `auto` one is applied by itself (the register may label it).
    `is_live_now` is the schedule (dates, weekday, time window) in the shop's
    time zone at the moment of the request; min spend is left to the check,
    via `trigger_value`.
    """

    id: uuid.UUID
    name: str
    reward: str
    #: A percent for `percentage_off_order` (15 == 15%), AED for a fixed one.
    reward_value: Decimal
    #: The minimum spend (pre-discount) the check needs.
    trigger_value: Decimal
    #: Categories it is confined to; empty = the whole order.
    category_ids: list[uuid.UUID]
    mode: Literal["coupon", "auto"]
    is_live_now: bool
