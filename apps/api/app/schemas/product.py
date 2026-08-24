from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.product import SALES_CHANNELS

from .category import CategoryResponse
from .modifier import ProductModifierResponse

SalesChannel = Literal["pos", "web"]

#: The kinds of text a product can ask a customer for.
#:
#: A `Literal` rather than a free string so the admin cannot invent a fourth
#: kind the storefront has no field for — every value here needs a label and a
#: placeholder in both languages before it means anything to a shopper.
PersonalisationType = Literal["handwritten_note"]

#: What fits on the card the team currently writes on. Overridable per product,
#: because the ceiling describes stationery rather than software.
DEFAULT_PERSONALISATION_MAX_LENGTH = 100


def _dedupe_channels(value: list[str] | None) -> list[str] | None:
    """
    Canonical order, no repeats.

    A multi-select can post the same channel twice; stored as-is it would make
    two products that sell in the same places compare unequal, and the console
    would show a duplicated chip.
    """
    if value is None:
        return None
    return [c for c in SALES_CHANNELS if c in value]


class ProductModifierLink(BaseModel):
    modifier_id: UUID
    minimum_options: int = 0
    maximum_options: int = 1
    free_options: int = 0
    unique_options: bool = False


class ProductCreate(BaseModel):
    category_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9-]+$")
    sku: str | None = None
    description: str | None = None
    translations: dict[str, dict[str, str]] = Field(default_factory=dict)
    base_price: Decimal = Field(ge=0, decimal_places=2, default=Decimal("0"))
    calories: int | None = None
    preparation_time: int | None = None
    is_sold_by_weight: bool = False
    #: "fixed" sells at base_price; "open" makes the cashier key the price in.
    pricing_method: Literal["fixed", "open"] = "fixed"
    is_stock_product: bool = False
    stock_quantity: int = Field(default=0, ge=0)
    image_urls: list[str] = Field(default_factory=list)
    is_featured: bool = False
    #: Which channels sell this — any subset of ("pos", "web"). Empty means
    #: the item is in the catalogue and not yet sold anywhere, which is a
    #: legitimate draft state rather than an error.
    sales_channels: list[SalesChannel] = Field(
        default_factory=lambda: list(SALES_CHANNELS)
    )
    #: Free-form nutrition panel: protein, carbs, fat, salt, allergens.
    nutrition: dict | None = None
    display_order: int = 0
    is_cart_addon: bool = False
    personalisation_type: PersonalisationType | None = None
    personalisation_max_length: int = Field(
        default=DEFAULT_PERSONALISATION_MAX_LENGTH, ge=1, le=500
    )

    _canonical_channels = field_validator("sales_channels")(_dedupe_channels)


class ProductUpdate(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    slug: str | None = Field(None, pattern=r"^[a-z0-9-]+$")
    sku: str | None = None
    description: str | None = None
    translations: dict[str, dict[str, str]] | None = None
    base_price: Decimal | None = Field(None, ge=0)
    calories: int | None = None
    preparation_time: int | None = None
    is_sold_by_weight: bool | None = None
    pricing_method: Literal["fixed", "open"] | None = None
    is_stock_product: bool | None = None
    stock_quantity: int | None = Field(None, ge=0)
    image_urls: list[str] | None = None
    is_active: bool | None = None
    is_featured: bool | None = None
    sales_channels: list[SalesChannel] | None = None
    #: Free-form nutrition panel: protein, carbs, fat, salt, allergens.
    nutrition: dict | None = None
    display_order: int | None = None
    is_cart_addon: bool | None = None
    personalisation_type: PersonalisationType | None = None
    personalisation_max_length: int | None = Field(None, ge=1, le=500)

    _canonical_channels = field_validator("sales_channels")(_dedupe_channels)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID | None
    name: str
    slug: str
    sku: str | None
    description: str | None
    translations: dict[str, dict[str, str]] = {}
    base_price: float
    calories: int | None
    preparation_time: int | None
    is_sold_by_weight: bool
    # The terminal needs this to know whether to prompt for a price. Without
    # it an open-price product looks like an ordinary one, the cashier adds it
    # with no amount, and the order engine rejects the line with no way for
    # them to satisfy it.
    pricing_method: str = "fixed"
    is_stock_product: bool
    stock_quantity: int
    image_urls: list[str]
    is_active: bool
    is_featured: bool
    sales_channels: list[SalesChannel] = Field(
        default_factory=lambda: list(SALES_CHANNELS)
    )
    nutrition: dict | None = None
    display_order: int
    is_cart_addon: bool = False
    #: What this product asks the customer to write, and how much of it fits.
    #: Carried on the response so the storefront can render the field, its label
    #: and its counter from the product it already has.
    personalisation_type: str | None = None
    personalisation_max_length: int = DEFAULT_PERSONALISATION_MAX_LENGTH
    created_at: datetime
    updated_at: datetime
    product_modifiers: list[ProductModifierResponse] = []
    category: CategoryResponse | None = None

    @property
    def has_modifiers(self) -> bool:
        return len(self.product_modifiers) > 0
