from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .category import Category
    from .cart import CartItem
    from .order import OrderItem


class Product(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    image_urls: Mapped[Any] = mapped_column(ARRAY(String), nullable=False, default=list, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    category: Mapped[Category] = relationship("Category", back_populates="products")
    variants: Mapped[list[ProductVariant]] = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Product {self.name}>"


class ProductVariant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    product: Mapped[Product] = relationship("Product", back_populates="variants")
    cart_items: Mapped[list[CartItem]] = relationship("CartItem", back_populates="variant")
    order_items: Mapped[list[OrderItem]] = relationship("OrderItem", back_populates="variant")

    def __repr__(self) -> str:
        return f"<ProductVariant {self.sku}>"
