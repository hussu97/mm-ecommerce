"""
Marketing: discounts, promotions, timed events, gift cards, loyalty and house
accounts.

Money that customers hold — gift card balances, loyalty points, house account
credit — is never stored as a single mutable number. Each has an append-only
transaction ledger and the balance is a projection of it, so a disputed balance
can always be reconstructed and explained.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


# ─── Gift cards ───────────────────────────────────────────────────────────────


class GiftCardStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    REDEEMED = "redeemed"  # balance exhausted
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class GiftCard(Base, UUIDMixin, TimestampMixin):
    """
    A stored-value card. `balance` is a cached projection of
    `GiftCardTransaction`; the ledger is authoritative.
    """

    __tablename__ = "gift_cards"

    code: Mapped[str] = mapped_column(
        String(40), unique=True, nullable=False, index=True
    )
    initial_balance: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    balance: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=GiftCardStatusEnum.ACTIVE.value,
        index=True,
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    issued_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transactions: Mapped[list[GiftCardTransaction]] = relationship(
        "GiftCardTransaction",
        back_populates="gift_card",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_usable(self) -> bool:
        return (
            self.status == GiftCardStatusEnum.ACTIVE.value
            and Decimal(str(self.balance)) > 0
        )

    def __repr__(self) -> str:
        return f"<GiftCard {self.code} {self.balance}>"


class GiftCardTransactionTypeEnum(str, enum.Enum):
    ISSUE = "issue"
    TOP_UP = "top_up"
    REDEEM = "redeem"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
    CANCEL = "cancel"


class GiftCardTransaction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "gift_card_transactions"

    gift_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gift_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: Signed: positive adds value, negative spends it.
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    gift_card: Mapped[GiftCard] = relationship(
        "GiftCard", back_populates="transactions"
    )

    def __repr__(self) -> str:
        return f"<GiftCardTransaction {self.type} {self.amount}>"


# ─── Loyalty ──────────────────────────────────────────────────────────────────


class LoyaltyProgram(Base, UUIDMixin, TimestampMixin):
    """
    Singleton-ish configuration for the points scheme.

    Points are earned per unit of spend and redeemed at a fixed value, which is
    the simplest model that customers reliably understand.
    """

    __tablename__ = "loyalty_programs"

    name: Mapped[str] = mapped_column(
        String(150), nullable=False, server_default="Melting Moments Rewards"
    )
    #: Points granted per 1.00 of qualifying spend.
    points_per_currency_unit: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="1"
    )
    #: Cash value of one point when redeemed.
    currency_per_point: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0.01"
    )
    minimum_points_to_redeem: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="100"
    )
    points_expire_after_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: Excludes discounted lines from earning, if the business wants that.
    earn_on_discounted_items: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    def __repr__(self) -> str:
        return f"<LoyaltyProgram {self.name}>"


class LoyaltyTransactionTypeEnum(str, enum.Enum):
    EARN = "earn"
    REDEEM = "redeem"
    ADJUSTMENT = "adjustment"
    EXPIRY = "expiry"
    REVERSAL = "reversal"


class LoyaltyTransaction(Base, UUIDMixin, TimestampMixin):
    """Append-only points ledger. A customer's balance is the sum of these."""

    __tablename__ = "loyalty_transactions"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: Signed: earning is positive, redemption negative.
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Cash value moved, for redemptions.
    amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LoyaltyTransaction {self.type} {self.points}>"


# ─── House accounts ───────────────────────────────────────────────────────────


class HouseAccount(Base, UUIDMixin, TimestampMixin):
    """
    A credit line for a corporate or regular customer: they sign for orders and
    settle periodically. `balance` is what they currently owe.
    """

    __tablename__ = "house_accounts"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_house_account_customer"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credit_limit: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    balance: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def available_credit(self) -> Decimal:
        return Decimal(str(self.credit_limit)) - Decimal(str(self.balance))

    def __repr__(self) -> str:
        return f"<HouseAccount customer={self.customer_id} owes={self.balance}>"


class HouseAccountTransactionTypeEnum(str, enum.Enum):
    CHARGE = "charge"  # an order signed to the account
    PAYMENT = "payment"  # the customer settles
    ADJUSTMENT = "adjustment"


class HouseAccountTransaction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "house_account_transactions"

    house_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("house_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: Signed: a charge increases what is owed, a payment reduces it.
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<HouseAccountTransaction {self.type} {self.amount}>"
