"""
Operational entities that close the last gaps against Foodics:
transfer orders, inventory spot checks, reservations and notification rules.
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
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class TransferOrderStatusEnum(str, enum.Enum):
    """
    Mirrors the Foodics transfer-order lifecycle exactly.

    A transfer order is a *request* between locations. Accepting it does not
    move stock — sending does, and receiving completes it. Keeping the request
    separate from the movement is what lets a branch dispute a short delivery.
    """

    DRAFT = "draft"
    PENDING = "pending"  # submitted to the source location
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CLOSED = "closed"  # sent and received


class TransferOrder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "transfer_orders"

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransferOrderStatusEnum.DRAFT.value,
        index=True,
    )
    #: Who is asking.
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Who is being asked.
    source_branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    required_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    responder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when the sending transaction is posted.
    sent_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )
    received_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="SET NULL"),
        nullable=True,
    )

    items: Mapped[list[TransferOrderItem]] = relationship(
        "TransferOrderItem",
        back_populates="transfer_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<TransferOrder {self.reference} {self.status}>"


class TransferOrderItem(Base, UUIDMixin):
    __tablename__ = "transfer_order_items"

    transfer_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transfer_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    #: What the source location actually agreed to send, which may be less.
    approved_quantity: Mapped[Any | None] = mapped_column(Numeric(16, 4), nullable=True)
    sent_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    received_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="storage"
    )
    conversion_factor: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="1"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transfer_order: Mapped[TransferOrder] = relationship(
        "TransferOrder", back_populates="items"
    )

    def __repr__(self) -> str:
        return f"<TransferOrderItem item={self.item_id} qty={self.quantity}>"


class SpotCheck(Base, UUIDMixin, TimestampMixin):
    """
    An audit team's independent count.

    Deliberately does **not** move stock — it exists purely to compare what the
    auditor found against what the system believes, so a discrepancy can be
    investigated before anyone adjusts the books.
    """

    __tablename__ = "spot_checks"

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="draft", index=True
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[SpotCheckItem]] = relationship(
        "SpotCheckItem",
        back_populates="spot_check",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<SpotCheck {self.reference}>"


class SpotCheckItem(Base, UUIDMixin):
    __tablename__ = "spot_check_items"

    spot_check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spot_checks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    counted_quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    #: Snapshotted at close so the comparison stays meaningful afterwards.
    system_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    variance: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    spot_check: Mapped[SpotCheck] = relationship("SpotCheck", back_populates="items")

    def __repr__(self) -> str:
        return f"<SpotCheckItem item={self.item_id} variance={self.variance}>"


class ReservationStatusEnum(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SEATED = "seated"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class Reservation(Base, UUIDMixin, TimestampMixin):
    """A table booking."""

    __tablename__ = "reservations"

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tables.id", ondelete="SET NULL"), nullable=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    guests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=ReservationStatusEnum.PENDING.value,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def is_active(self) -> bool:
        return self.status in {
            ReservationStatusEnum.PENDING.value,
            ReservationStatusEnum.CONFIRMED.value,
            ReservationStatusEnum.SEATED.value,
        }

    def __repr__(self) -> str:
        return f"<Reservation {self.reference} {self.starts_at}>"


class NotificationRule(Base, UUIDMixin, TimestampMixin):
    """
    Who gets told when something happens — a void over a threshold, a till
    variance, stock below minimum. Foodics calls these notification rules.
    """

    __tablename__ = "notification_rules"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    #: Domain event, e.g. "order.voided", "till.closed", "inventory.below_minimum".
    event: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    #: Only fire above this value, where the event carries an amount.
    threshold: Mapped[Any | None] = mapped_column(Numeric(12, 2), nullable=True)
    branch_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    recipient_user_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    recipient_emails: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    channels: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{email}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    meta: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<NotificationRule {self.event}>"
