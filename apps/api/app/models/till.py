from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .user import User


class TillStatusEnum(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class Till(Base, UUIDMixin, TimestampMixin):
    """
    One cashier's session on one device for one business day.

    The cash reconciliation is:
        estimated_cash = opening_amount
                       + cash payments
                       - cash refunds
                       + pay-ins
                       - pay-outs
                       - cash drops
        variance       = closing_amount - estimated_cash

    `estimated_cash` is recomputed from the ledger on close rather than being
    incremented in place, so a crashed terminal can never desynchronise it.
    """

    __tablename__ = "tills"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=TillStatusEnum.OPEN.value, index=True
    )

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    opening_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    closing_amount: Mapped[Any | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_cash: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    variance: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )

    # Frozen X/Z-report figures written at close.
    totals: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    drawer_operations: Mapped[list[DrawerOperation]] = relationship(
        "DrawerOperation", back_populates="till", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.status == TillStatusEnum.OPEN.value

    def __repr__(self) -> str:
        return f"<Till {self.business_date} {self.status}>"


class DrawerOperationTypeEnum(str, enum.Enum):
    PAY_IN = "pay_in"
    PAY_OUT = "pay_out"
    CASH_DROP = "cash_drop"  # cash moved to the safe
    OPEN_DRAWER = "open_drawer"  # no-sale, audit only
    SALES = "sales"  # cash taken as payment
    RETURN = "return"  # cash refunded
    SPOT_CHECK = "spot_check"  # mid-shift count, audit only


#: Sign each operation type contributes to the expected cash in the drawer.
DRAWER_SIGN: dict[str, int] = {
    DrawerOperationTypeEnum.PAY_IN.value: 1,
    DrawerOperationTypeEnum.SALES.value: 1,
    DrawerOperationTypeEnum.PAY_OUT.value: -1,
    DrawerOperationTypeEnum.CASH_DROP.value: -1,
    DrawerOperationTypeEnum.RETURN.value: -1,
    DrawerOperationTypeEnum.OPEN_DRAWER.value: 0,
    # A spot check counts the drawer, it does not move anything in or out.
    DrawerOperationTypeEnum.SPOT_CHECK.value: 0,
}


class DrawerOperation(Base, UUIDMixin, TimestampMixin):
    """Every movement of physical cash, including no-sale drawer opens."""

    __tablename__ = "drawer_operations"

    till_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reasons.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    till: Mapped[Till] = relationship("Till", back_populates="drawer_operations")

    @property
    def signed_amount(self) -> Decimal:
        return Decimal(str(self.amount)) * DRAWER_SIGN.get(self.type, 0)

    def __repr__(self) -> str:
        return f"<DrawerOperation {self.type} {self.amount}>"
