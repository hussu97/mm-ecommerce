from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, TimestampMixin, utcnow

if TYPE_CHECKING:
    from .user import User
    from .product import ProductVariant


class Cart(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "carts"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="carts")
    items: Mapped[list[CartItem]] = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Cart {self.id}>"


class CartItem(Base, UUIDMixin):
    __tablename__ = "cart_items"

    cart_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    cart: Mapped[Cart] = relationship("Cart", back_populates="items")
    variant: Mapped[ProductVariant] = relationship("ProductVariant", back_populates="cart_items")

    def __repr__(self) -> str:
        return f"<CartItem {self.variant_id} x{self.quantity}>"
