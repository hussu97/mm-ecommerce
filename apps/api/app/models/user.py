from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .address import Address
    from .admin_passkey import AdminPasskey
    from .cart import Cart
    from .order import Order
    from .role import Role


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ─── Staff / POS identity ─────────────────────────────────────────────────
    # A staff member is a User with is_staff=True. They sign in to the console
    # with email + password and to the POS terminal with a numeric PIN.
    is_staff: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    display_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    staff_number: Mapped[str | None] = mapped_column(
        String(30), unique=True, nullable=True, index=True
    )
    # Hashed with the same password hasher — never store the PIN in clear text.
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_driver: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )

    # Relationships
    addresses: Mapped[list[Address]] = relationship(
        "Address", back_populates="user", cascade="all, delete-orphan"
    )
    carts: Mapped[list[Cart]] = relationship("Cart", back_populates="user")
    orders: Mapped[list[Order]] = relationship("Order", back_populates="user")
    admin_passkeys: Mapped[list[AdminPasskey]] = relationship(
        "AdminPasskey", back_populates="user", cascade="all, delete-orphan"
    )
    role: Mapped[Role | None] = relationship("Role", lazy="selectin")

    def can(self, permission: str) -> bool:
        """Permission check for staff. Console admins keep unrestricted access."""
        if self.is_admin:
            return True
        return bool(self.role and self.role.has(permission))

    def __repr__(self) -> str:
        return f"<User {self.email}>"
