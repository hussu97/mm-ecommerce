from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin, utcnow

if TYPE_CHECKING:
    from .user import User
    from .product import Product


class Cart(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "carts"

    #: A cart names whose it is — an account, or a guest session, never
    #: neither. A row with both NULL is matched by `session_id IS NULL`, which
    #: is what a lookup for a shopper carrying no session id compiles to, and
    #: production accumulated four customers' items in one such row before
    #: migration 117 removed it. Mirrored from that migration.
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR (session_id IS NOT NULL AND session_id <> '')",
            name="ck_carts_has_identity",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    #: The address typed into the checkout form by somebody with no account.
    #:
    #: Written back from `POST /orders/preview`, which already asked for it in
    #: order to judge a new-customer coupon and then dropped it. It is the only
    #: thing that makes an abandoned guest basket reachable — see migration 116.
    #:
    #: Never written for a basket that has a `user_id`: that one already has an
    #: address on `users.email`, and a copy here would be free to go stale the
    #: day somebody changes their account email. Read precedence is therefore
    #: the account first and this second, and the two can never disagree.
    guest_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    #: When the person holding this basket last touched it.
    #:
    #: **Not `updated_at`.** Adding a line writes `cart_items` and never touches
    #: this row, so a basket that has been actively filled for ten minutes can
    #: carry an `updated_at` from the moment it was created. Stamped instead by
    #: `cart_service.touch` on every read and every write of the basket, which
    #: makes it the answer to the only question a stale basket raises: how long
    #: has it been sitting there.
    #:
    #: Throttled to a minute's resolution at the point of writing, because
    #: `GET /cart` fires on every page load and a column this cheap is not worth
    #: one row update per page view.
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    #: The promo code the customer applied in the basket, if any.
    #:
    #: The **code**, never the discount. What a coupon is worth depends on the
    #: basket, the identity and the day, and it is recomputed every time it is
    #: asked; a copy of the amount here would be a second answer free to drift
    #: from the first. See migration 115 for the failure this exists to close.
    promo_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ─── Live courier estimate ────────────────────────────────────────────────
    # What a real courier said this basket would cost to deliver to the pin the
    # customer had dropped, captured while they were still deciding. It is
    # never shown to them and never charged — the fee they see comes from the
    # zone map. It is recorded because the two numbers have to be compared
    # somewhere, and a basket that was abandoned is as informative as one that
    # converted: an order table only ever knows about the baskets that paid.
    delivery_quote_provider: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    delivery_quote_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: What we would have charged, at the moment of the quote.
    delivery_quote_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    #: What the courier said it would cost us.
    delivery_quote_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    delivery_quote_currency: Mapped[str | None] = mapped_column(
        String(3), nullable=True
    )
    delivery_quote_distance_m: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    #: Lalamove's `quotationId`. Expires after five minutes, so it is a receipt
    #: for the estimate rather than something to book against later.
    delivery_quote_reference: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    delivery_quote_latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )
    delivery_quote_longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )
    delivery_quote_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Why the last estimate could not be taken — an unserviceable address, a
    #: timeout. Recorded rather than swallowed: "no quote" and "quote of zero"
    #: are very different findings.
    delivery_quote_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="carts")
    items: Mapped[list[CartItem]] = relationship(
        "CartItem", back_populates="cart", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Cart {self.id}>"


class CartItem(Base, UUIDMixin):
    __tablename__ = "cart_items"

    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("carts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    selected_options: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    #: What the customer wants written, for a product whose
    #: `personalisation_type` asks for it. Null everywhere else.
    #:
    #: This participates in the line's dedup key — see `cart_service`. Two gift
    #: notes carrying different messages are two lines, never one line of two.
    personalisation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    cart: Mapped[Cart] = relationship("Cart", back_populates="items")
    product: Mapped[Product] = relationship("Product")

    def __repr__(self) -> str:
        return f"<CartItem product={self.product_id} x{self.quantity}>"
