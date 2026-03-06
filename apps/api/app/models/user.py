from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .address import Address
    from .cart import Cart
    from .order import Order


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    addresses: Mapped[list[Address]] = relationship(
        "Address", back_populates="user", cascade="all, delete-orphan"
    )
    carts: Mapped[list[Cart]] = relationship("Cart", back_populates="user")
    orders: Mapped[list[Order]] = relationship("Order", back_populates="user")

    def __repr__(self) -> str:
        return f"<User {self.email}>"
