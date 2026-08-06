from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .category import Category
    from .modifier import ProductModifier


#: Every channel a product can be sold on. The order is the order the console
#: offers them in. A database check constraint holds the same set, so a typo in
#: an import is rejected rather than becoming a product that sells nowhere.
SALES_CHANNELS: tuple[str, ...] = ("pos", "web")

POS_CHANNEL = "pos"
WEB_CHANNEL = "web"


class Product(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "products"

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    sku: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    base_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    cost: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preparation_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Whether ordering this needs a date and a brief rather than a basket.
    #:
    #: A customisable product is a day of somebody's work, so the storefront
    #: makes the customer choose a date, checks that date still has capacity,
    #: and collects the design details. Everything else behaves exactly as it
    #: did — this flag is the only thing that turns the extra flow on.
    is_customisable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    #: Days of notice this product needs, counted from today. Null falls back to
    #: the business-wide default.
    #:
    #: `preparation_time` is minutes in the kitchen and is a different question
    #: — a cake can take two hours to make and still need three days' notice
    #: because the diary is full.
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_urls: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_sold_by_weight: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    #: Which channels sell this product — any subset of `SALES_CHANNELS`.
    #:
    #: The POS catalogue and the storefront catalogue are not the same list. A
    #: coffee shop sells lattes and bottled water over the counter that the
    #: cake website has no business listing, and the website sells nine-piece
    #: boxes the counter does not. One list rather than a flag per channel, so
    #: "where is this sold" is a single answer an operator can read and set in
    #: one place — and so adding a channel is data rather than a migration.
    #:
    #: An empty list is legitimate: an item in the catalogue, sold nowhere yet.
    #: On the register this is still only half the answer — the menu tree
    #: decides layout and can switch a whole branch off; see
    #: `menu_group_service.pos_visibility_clause`.
    sales_channels: Mapped[Any] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=lambda: list(SALES_CHANNELS),
        server_default="{pos,web}",
    )
    is_stock_product: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    stock_quantity: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ─── POS fields ───────────────────────────────────────────────────────────
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Which taxes apply. Null means the product is untaxed.
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # "fixed" uses base_price; "open" makes the cashier key the price at sale time.
    pricing_method: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="fixed"
    )
    # "fixed" uses `cost`; "ingredients" derives it from the recipe.
    costing_method: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="fixed"
    )
    # Staff meals, comps and packaging: never taxed, never counted as revenue.
    is_non_revenue: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="false"
    )
    #: Full nutrition panel. Free-form so adding sugar or salt later
    #: needs no migration — the shape follows the packet, not us.
    nutrition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    walking_minutes_to_burn_calories: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Relationships
    category: Mapped[Category] = relationship("Category", back_populates="products")
    product_modifiers: Mapped[list[ProductModifier]] = relationship(
        "ProductModifier", back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Product {self.name}>"


def sells_on(channel: str):
    """
    SQL for "this product is sold on `channel`".

    Written as `@>` rather than `= ANY(...)` so the GIN index on
    `sales_channels` is usable — the storefront runs this on every listing.

    It lives here, beside the column, because the product, category and menu
    tree services all ask the question and importing between them would be a
    cycle. One definition means the storefront and the register can never
    disagree about what "sold on the web" means.
    """
    return Product.sales_channels.contains([channel])
