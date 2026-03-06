from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow


class DiscountTypeEnum(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class PromoCode(Base, UUIDMixin):
    __tablename__ = "promo_codes"
    __table_args__ = (
        CheckConstraint("discount_value > 0", name="ck_promo_discount_positive"),
    )

    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    discount_type: Mapped[DiscountTypeEnum] = mapped_column(
        Enum(
            DiscountTypeEnum,
            name="discounttypeenum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    discount_value: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    min_order_amount: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<PromoCode {self.code}>"
