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

from .base import Base, TimestampMixin, UUIDMixin


class DiscountTypeEnum(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class PromoCode(Base, UUIDMixin, TimestampMixin):
    # `TimestampMixin` (migration 101) replaces the hand-rolled `created_at`
    # this table carried, and adds the `updated_at` it lacked: a coupon's
    # ceiling, expiry and active flag are all edited in place, and "when did
    # this code last change" had no answer.
    __tablename__ = "promo_codes"
    __table_args__ = (
        CheckConstraint("discount_value > 0", name="ck_promo_discount_positive"),
        # A percentage over 100 drives the subtotal, and then the VAT, negative.
        # The API refuses one; this refuses one written straight to the table.
        CheckConstraint(
            "discount_type <> 'percentage' OR discount_value <= 100",
            name="ck_promo_percentage_ceiling",
        ),
    )

    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    #: The same coupon, spelled in Arabic. Optional, and when set it is a second
    #: way of naming *this* row rather than a second coupon — every limit and
    #: every count is shared, so a customer cannot redeem the English code and
    #: then the Arabic one.
    #:
    #: A separate column rather than a translations table because a coupon code
    #: is not prose: there are exactly two of them, they are matched exactly, and
    #: both have to be uniquely indexed against the same namespace as `code`.
    code_ar: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
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
    #: The most this code can ever take off, in AED. Only meaningful on a
    #: percentage discount, where the amount otherwise scales with the basket:
    #: 15% of a 60-dirham order is 9, and 15% of a 600-dirham catering order is
    #: 90. NULL means uncapped, which is what every code was before this existed.
    max_discount_amount: Mapped[Any | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How many times one customer may redeem this code. NULL means unlimited,
    #: which is what every existing code was: `max_uses` is a campaign-wide
    #: ceiling, so one customer could redeem a 500-use code 500 times.
    max_uses_per_user: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Restricts the code to a customer's first N orders. NULL means no such
    #: restriction.
    #:
    #: Different from `max_uses_per_user` in what it counts. That one asks "how
    #: often have you used *this code*"; this asks "how many orders have you
    #: placed at all", which is what makes a code an acquisition offer rather
    #: than a loyalty one. A customer with three orders behind them does not get
    #: it, whether or not they have ever seen it.
    #:
    #: Identity is user, email and phone together — see
    #: `promo_code_service.orders_placed_by`. Keying on the account alone would
    #: make the limit meaningless, because guest checkout mints a fresh user row
    #: for every session.
    first_orders_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Whether the customer must have proved their phone number to redeem this.
    #:
    #: The point of a new-customer offer is that it is claimed once, and the
    #: only identity in the whole checkout that costs anything to fake is a
    #: phone number — an email is free and infinite, an account more so. Without
    #: this, `first_orders_limit` is enforced against identities a determined
    #: person can mint at will.
    #:
    #: False by default: an ordinary coupon should not start demanding an OTP
    #: because this column arrived.
    requires_phone_verification: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    current_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PromoCode {self.code}>"
