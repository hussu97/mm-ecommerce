"""Locale-resolved view models handed from the builder to the renderer.

Deliberately free of any app/ORM import: the renderer (Jinja + WeasyPrint) and
the offline sample harness depend on these dataclasses without dragging in the
database layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .theme import Theme


@dataclass
class Variant:
    label: str
    price: str


@dataclass
class MenuItem:
    name: str
    description: str | None
    image_url: str | None
    image_data_uri: str | None  # filled by render.prepare_menu_assets
    single_price: str | None
    price_prefix: str | None  # "From" / "من" or None
    variants: list[Variant]
    column_prices: list[str | None] | None  # aligned to section.columns when set
    calories: int | None


@dataclass
class MenuColumnBlock:
    """One run of items inside a section that share a size signature.

    A block prints its `columns` (e.g. ``S / M / L``) as a single header row and
    every item's `column_prices` lines up under it. `columns is None` is a plain
    block — single-price rows or lone-variant chips — with no header. Splitting a
    section into blocks is what lets a "Hot Coffee" section carry both an S/M/L
    grid and a Single/Double grid without repeating the sizes on every line.
    """

    columns: list[str] | None
    items: list[MenuItem]


@dataclass
class MenuSection:
    title: str
    icon_key: str
    blocks: list[MenuColumnBlock]


@dataclass
class MenuDocument:
    lang: str
    direction: str  # "ltr" | "rtl"
    theme: Theme
    brand_name: str
    logo_url: str | None
    logo_data_uri: str | None  # filled by render.prepare_menu_assets
    title: str  # localized "Menu"
    branch_name: str | None
    whatsapp_number: str | None
    whatsapp_url: str | None
    qr_svg: str | None  # filled by render.prepare_menu_assets
    sections: list[MenuSection] = field(default_factory=list)

    @property
    def has_contact(self) -> bool:
        return bool(self.whatsapp_url)
