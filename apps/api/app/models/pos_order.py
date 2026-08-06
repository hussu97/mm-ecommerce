"""
POS order extensions.

Web and POS orders share one `orders` table — the business has one order ledger,
and reporting must not have to union two shapes. The columns added here are
POS-only and stay null for storefront orders.

`status` keeps its existing e-commerce vocabulary (created/confirmed/packed/…)
so nothing about the storefront changes. POS orders additionally carry
`pos_status`, which models the very different counter lifecycle
(draft → pending → active → closed, plus void/returned/joined).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class OrderTypeEnum(str, enum.Enum):
    DINE_IN = "dine_in"
    PICKUP = "pickup"
    DELIVERY = "delivery"
    DRIVE_THRU = "drive_thru"


class OrderSourceEnum(str, enum.Enum):
    CASHIER = "cashier"
    ONLINE = "online"  # the Melting Moments storefront
    API = "api"
    CALL_CENTER = "call_center"


class PosOrderStatusEnum(str, enum.Enum):
    DRAFT = "draft"  # parked, never sent anywhere
    PENDING = "pending"  # awaiting acceptance (online/call centre)
    ACTIVE = "active"  # open check on the floor
    CLOSED = "closed"  # fully paid and finished
    DECLINED = "declined"
    VOID = "void"
    RETURNED = "returned"
    JOINED = "joined"  # merged into another check


class OrderItemStatusEnum(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CLOSED = "closed"
    MOVED = "moved"  # transferred to another check during a split
    VOID = "void"
    RETURNED = "returned"
    DECLINED = "declined"


class DeliveryStatusEnum(str, enum.Enum):
    SENT_TO_KITCHEN = "sent_to_kitchen"
    READY = "ready"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    DELIVERED = "delivered"
    CLOSED = "closed"


class DiscountSourceEnum(str, enum.Enum):
    OPEN = "open"
    PREDEFINED = "predefined"
    COUPON = "coupon"
    LOYALTY = "loyalty"
    PROMOTION = "promotion"


class OrderPayment(Base, UUIDMixin, TimestampMixin):
    """
    One tender against an order. An order may have many, which is what makes
    split payment ("50 cash, rest on card") possible.

    `tendered` records what the customer handed over so change can be shown on the
    receipt; `amount` is what was actually applied to the balance.
    """

    __tablename__ = "order_payments"
    __table_args__ = (
        Index(
            "uq_order_payments_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_method_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payment_methods.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    till_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tills.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    tendered: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tips: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    change_given: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    is_refund: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Client-supplied key that makes a retried payment a no-op.
    #:
    #: `RegisterModel.pay` is a three-call sequence over a 15-second timeout: a
    #: payment the server actually recorded can time out on the way back, and
    #: the cashier — looking at a check that still says unpaid — takes the money
    #: again. The `amount > outstanding` guard catches that for a single tender
    #: and does nothing at all for a split, where the second half is a
    #: legitimately smaller amount against a balance that is legitimately lower.
    #:
    #: NULL for anything that does not send one, and the unique index is partial
    #: so those rows do not collide with each other.
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    payment_method = relationship("PaymentMethod", lazy="selectin")

    @property
    def signed_amount(self) -> Decimal:
        value = Decimal(str(self.amount))
        return -value if self.is_refund else value

    def __repr__(self) -> str:
        return f"<OrderPayment {self.amount} refund={self.is_refund}>"


class OrderCharge(Base, UUIDMixin, TimestampMixin):
    """A charge applied to an order, snapshotted so later config edits can't rewrite history."""

    __tablename__ = "order_charges"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    charge_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("charges.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tax_exclusive_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )

    def __repr__(self) -> str:
        return f"<OrderCharge {self.name} {self.amount}>"


class OrderDiscount(Base, UUIDMixin, TimestampMixin):
    """
    A discount applied to an order or to one of its lines.

    `source` records where it came from (open, predefined, coupon, loyalty,
    promotion) which is exactly the breakdown the discount report needs.
    """

    __tablename__ = "order_discounts"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    is_percentage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    value: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)
    applied_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<OrderDiscount {self.source} {self.amount}>"


class OrderTax(Base, UUIDMixin, TimestampMixin):
    """
    Per-tax totals for an order, one row per distinct rate.

    Stored rather than derived because a VAT return must reproduce exactly what
    was printed on the invoice, even if the rate changes afterwards.
    """

    __tablename__ = "order_taxes"
    __table_args__ = (UniqueConstraint("order_id", "tax_id", name="uq_order_tax"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("taxes.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rate: Mapped[Any] = mapped_column(Numeric(6, 4), nullable=False)
    taxable_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    amount: Mapped[Any] = mapped_column(Numeric(12, 2), nullable=False)

    def __repr__(self) -> str:
        return f"<OrderTax {self.name} {self.amount}>"


class KitchenTicket(Base, UUIDMixin, TimestampMixin):
    """
    A batch of lines sent to one kitchen station.

    A check that gets a starter fired now and a main fired later produces two
    tickets, which is what the KDS bumps independently.
    """

    __tablename__ = "kitchen_tickets"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kitchen_flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kitchen_flows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="new", index=True
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reprint_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[KitchenTicketItem]] = relationship(
        "KitchenTicketItem",
        back_populates="ticket",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<KitchenTicket order={self.order_id} #{self.sequence}>"


class KitchenTicketItem(Base, UUIDMixin, TimestampMixin):
    """One order line on a kitchen ticket, bumpable on its own."""

    __tablename__ = "kitchen_ticket_items"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kitchen_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("order_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    modifiers_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="new"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    ticket: Mapped[KitchenTicket] = relationship(
        "KitchenTicket", back_populates="items"
    )

    def __repr__(self) -> str:
        return f"<KitchenTicketItem {self.product_name} x{self.quantity}>"


class KitchenTicketStatusEnum(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
