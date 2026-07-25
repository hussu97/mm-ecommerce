from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class PaymentMethodTypeEnum(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    GIFT_CARD = "gift_card"
    HOUSE_ACCOUNT = "house_account"
    ONLINE = "online"
    OTHER = "other"


class PaymentMethod(Base, UUIDMixin, TimestampMixin):
    """
    A tender type the cashier can settle an order with.

    `type` drives behaviour, not just labelling:
      * cash          — counts toward the till's expected cash, can open the drawer
      * gift_card     — draws down a GiftCard balance
      * house_account — posts to the customer's house account ledger
      * card/online   — settled by a processor, never affects the cash drawer
    """

    __tablename__ = "payment_methods"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    auto_open_drawer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Cash tendering shows a "quick cash" pad; card tendering does not.
    allows_tendering: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    allows_tips: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    allows_refund: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Cash is usually rounded to the nearest 0.25 AED; card is not.
    rounding_step: Mapped[Any | None] = mapped_column(String(10), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def affects_cash_drawer(self) -> bool:
        return self.type == PaymentMethodTypeEnum.CASH.value

    def __repr__(self) -> str:
        return f"<PaymentMethod {self.code} ({self.type})>"
