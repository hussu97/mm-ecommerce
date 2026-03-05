from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .category import Category
    from .modifier import ProductModifier


class Product(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(300), nullable=True)
    slug: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    sku: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_localized: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    cost: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preparation_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_urls: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sold_by_weight: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_stock_product: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    category: Mapped[Category] = relationship("Category", back_populates="products")
    product_modifiers: Mapped[list[ProductModifier]] = relationship(
        "ProductModifier", back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Product {self.name}>"
