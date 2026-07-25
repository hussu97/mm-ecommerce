from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class ChargeTypeEnum(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"
    OPEN = "open"  # cashier types the amount at order time


class Charge(Base, UUIDMixin, TimestampMixin):
    """
    An amount added to an order that is not a product — delivery fee, service
    charge, packaging fee.

    `order_types` scopes the charge (e.g. a delivery fee only applies to delivery
    orders); an empty array means "all order types".
    """

    __tablename__ = "charges"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[Any] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
    )
    is_auto_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    order_types: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tax_group = relationship("TaxGroup")

    def applies_to(self, order_type: str) -> bool:
        return not self.order_types or order_type in self.order_types

    def __repr__(self) -> str:
        return f"<Charge {self.name} {self.type}={self.value}>"
