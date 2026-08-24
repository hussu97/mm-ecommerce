"""
Marketing: discounts, promotions and timed events.

Gift cards, loyalty and house accounts used to live here too. All three were
fully built — models, ledgers, services, routers — and never wired into a single
selling path: no order ever earned a point, no payment ever drew down a balance.
A gift-card tender selected at the counter settled the check against nothing at
all. They were removed rather than left as a money-shaped hole waiting for
someone to pick them from a dropdown.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class DiscountQualificationEnum(str, enum.Enum):
    PRODUCT = "product"
    ORDER = "order"
    BOTH = "both"


class Discount(Base, UUIDMixin, TimestampMixin):
    """
    A pre-defined discount a cashier can apply with one tap, as opposed to an
    open discount they type themselves.
    """

    __tablename__ = "discounts"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    qualification: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=DiscountQualificationEnum.ORDER.value,
    )
    #: Fraction when `is_percentage`, otherwise an absolute amount.
    amount: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    is_percentage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_taxable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    minimum_order_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    minimum_product_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    #: Caps a percentage discount, e.g. "20% off, up to 50 AED".
    maximum_amount: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    #: Empty means every branch / every order type.
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    order_types: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def applies_to(self, branch_id: uuid.UUID | None, order_type: str | None) -> bool:
        if self.branch_ids and branch_id not in self.branch_ids:
            return False
        if self.order_types and order_type not in self.order_types:
            return False
        return self.is_active and self.deleted_at is None

    def __repr__(self) -> str:
        return f"<Discount {self.name}>"


class ScheduleMixin:
    """Shared day/time/date windowing for promotions and timed events."""

    from_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Minutes from midnight, so a window can cross midnight coherently.
    from_time: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    to_time: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1439")
    is_mon: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_tue: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_wed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_thu: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_fri: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_sat: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_sun: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def runs_on(self, weekday: int) -> bool:
        """`weekday` is Python's Monday=0 convention."""
        return [
            self.is_mon,
            self.is_tue,
            self.is_wed,
            self.is_thu,
            self.is_fri,
            self.is_sat,
            self.is_sun,
        ][weekday]

    def runs_at(self, minutes_from_midnight: int) -> bool:
        if self.from_time <= self.to_time:
            return self.from_time <= minutes_from_midnight <= self.to_time
        # Window crosses midnight, e.g. 22:00 → 02:00.
        return (
            minutes_from_midnight >= self.from_time
            or minutes_from_midnight <= self.to_time
        )


class PromotionTypeEnum(str, enum.Enum):
    BASIC = "basic"
    ADVANCED = "advanced"


class PromotionTriggerEnum(str, enum.Enum):
    QUANTITY = "quantity"  # buy N items
    SPEND = "spend"  # spend N amount


class PromotionRewardEnum(str, enum.Enum):
    PERCENTAGE_OFF_PRODUCTS = "percentage_off_products"
    FIXED_OFF_PRODUCTS = "fixed_off_products"
    PERCENTAGE_OFF_ORDER = "percentage_off_order"
    FIXED_OFF_ORDER = "fixed_off_order"
    FIXED_PRICE = "fixed_price"
    FREE_PRODUCT = "free_product"


class Promotion(Base, UUIDMixin, TimestampMixin, ScheduleMixin):
    """
    A conditional offer — "buy 2 get 1 free", "10% off over 100 AED".

    Unlike a discount, a promotion applies itself when its condition is met
    rather than being chosen by the cashier.
    """

    __tablename__ = "promotions"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=PromotionTypeEnum.BASIC.value
    )
    trigger: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=PromotionTriggerEnum.SPEND.value
    )
    trigger_value: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    reward: Mapped[str] = mapped_column(String(40), nullable=False)
    reward_value: Mapped[Any] = mapped_column(
        Numeric(12, 4), nullable=False, server_default="0"
    )
    #: Products the trigger counts and/or the reward applies to; empty = any.
    trigger_product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    reward_product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    order_types: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    customer_tag_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    #: The order channels this promotion may fire on — `OrderSourceEnum` values
    #: (`cashier`, `online`, `aggregator`, …). Empty = every channel.
    #:
    #: `order_types` already scopes the *fulfilment shape* (dine-in vs delivery);
    #: this scopes *who rang it up*, which is the axis that separates a walk-in at
    #: the register from a storefront or aggregator order sharing the same table.
    #: A counter-only promotion carries `["cashier"]` — the one source the POS
    #: itself creates — so the storefront and the aggregators never inherit it.
    sources: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    #: Whether the pricing engine applies this promotion by itself, with no
    #: cashier action. An ordinary promotion is a rule the counter *may* invoke;
    #: an auto-apply one is a standing discount the register puts on every
    #: qualifying check — "every counter order is 15% off" — so it is enforced in
    #: `pos_order_service.recalculate`, not chosen from a menu.
    #:
    #: Only meaningful for an order-level reward (`percentage_off_order`,
    #: `fixed_off_order`) fired on a `spend` trigger: those are the rewards that
    #: reduce to one order-level `OrderDiscount` the engine can add unattended.
    #: The API refuses `auto_apply` on any other reward/trigger so the flag can
    #: never be set on a shape the engine will silently ignore.
    auto_apply: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: Lower number wins when several promotions could apply.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    #: Stops a single check from stacking the same offer indefinitely.
    max_uses_per_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def matches_order(
        self,
        *,
        source: str | None,
        branch_id: uuid.UUID | None,
        order_type: str | None,
    ) -> bool:
        """
        Whether this promotion's channel/branch/type scope covers an order.

        Scope only — the caller still checks `is_active`, `deleted_at`, the
        schedule (`runs_on`/`runs_at`) and the spend trigger. Kept here so the
        "empty array means everything" rule lives with the columns it reads,
        the same way `Discount.applies_to` does.
        """
        if self.deleted_at is not None or not self.is_active:
            return False
        if self.sources and source not in self.sources:
            return False
        if self.branch_ids and branch_id not in self.branch_ids:
            return False
        if self.order_types and order_type not in self.order_types:
            return False
        return True

    def __repr__(self) -> str:
        return f"<Promotion {self.name}>"


class TimedEventTypeEnum(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"
    FIXED_PRICE = "fixed_price"


class TimedEvent(Base, UUIDMixin, TimestampMixin, ScheduleMixin):
    """
    A scheduled price change — happy hour, a lunch offer. Applies to the listed
    products whenever the schedule is live.
    """

    __tablename__ = "timed_events"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Any] = mapped_column(
        Numeric(12, 4), nullable=False, server_default="0"
    )
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    category_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    order_types: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<TimedEvent {self.name}>"
