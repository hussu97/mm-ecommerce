from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .product import Product


class Category(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    reference: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Whether this category is pushed to the delivery marketplaces by the catalog
    #: sync (its own switch, like `Product.sync_to_aggregators`). A category that
    #: syncs carries its opted-in products; off by default. `sync_channels` may
    #: restrict it to specific channels, or null for all. See
    #: `services/aggregators/catalog_sync.py`.
    sync_to_aggregators: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    sync_channels: Mapped[Any | None] = mapped_column(ARRAY(String), nullable=True)

    # Relationships
    products: Mapped[list[Product]] = relationship("Product", back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.name}>"
