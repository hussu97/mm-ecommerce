from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .product import Product


class Modifier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "modifiers"

    reference: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    options: Mapped[list[ModifierOption]] = relationship(
        "ModifierOption",
        back_populates="modifier",
        cascade="all, delete-orphan",
        order_by="ModifierOption.display_order",
    )
    product_modifiers: Mapped[list[ProductModifier]] = relationship(
        "ProductModifier", back_populates="modifier", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Modifier {self.name}>"


class ModifierOption(Base, UUIDMixin):
    __tablename__ = "modifier_options"

    modifier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sku: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    cost: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    modifier: Mapped[Modifier] = relationship("Modifier", back_populates="options")

    def __repr__(self) -> str:
        return f"<ModifierOption {self.sku}>"


class ProductModifier(Base, UUIDMixin):
    __tablename__ = "product_modifiers"
    __table_args__ = (
        UniqueConstraint("product_id", "modifier_id", name="uq_product_modifier"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    modifier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    minimum_options: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    maximum_options: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    free_options: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unique_options: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    product: Mapped[Product] = relationship(
        "Product", back_populates="product_modifiers"
    )
    modifier: Mapped[Modifier] = relationship(
        "Modifier", back_populates="product_modifiers"
    )

    def __repr__(self) -> str:
        return (
            f"<ProductModifier product={self.product_id} modifier={self.modifier_id}>"
        )
