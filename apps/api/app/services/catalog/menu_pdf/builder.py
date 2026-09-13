"""Assemble a locale-resolved view model of a menu-group root for rendering.

Everything locale- and price-dependent is decided here so the Jinja template is
dumb: it prints strings and numbers already chosen for one language. Prices are
read the way the register reads them — ``base_price`` plus modifier-option prices
— never a per-channel column, because MM keeps one price and enforces parity onto
the Grubtech tag (so the integrator menu prints the same numbers the counter
does).
"""

from __future__ import annotations

import uuid
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.menu import MenuGroup
from app.models.product import Product
from app.services.catalog import menu_group_service, product_service
from app.services.orders import tax_identity_service

from .icons import icon_key_for
from .theme import theme_for_entity_reference
from .view_models import MenuDocument, MenuItem, MenuSection, Variant

# A section prints an aligned size-column grid (S/M/L) instead of inline chips
# only when the sizes are shared enough to line up — otherwise the grid is mostly
# blanks. Below this share of the section's variant items, fall back to inline.
_COLUMN_SHARE_THRESHOLD = 0.6


_STRINGS = {
    "en": {"menu": "Menu", "from": "From", "order": "Order on WhatsApp", "aed": "AED"},
    "ar": {
        "menu": "قائمة الطعام",
        "from": "من",
        "order": "اطلب عبر واتساب",
        "aed": "د.إ",
    },
}


def _t(lang: str, key: str) -> str:
    return _STRINGS.get(lang, _STRINGS["en"])[key]


def _localized(obj: Any, field_name: str, lang: str) -> str | None:
    """`translations[lang][field]`, falling back to the base column.

    English always reads the base column; Arabic reads the JSONB translation and
    falls back to `name_localized` (for names) then the base value, matching every
    other localized read in the codebase.
    """
    base = getattr(obj, field_name, None)
    if lang == "en":
        return base
    tr = getattr(obj, "translations", None)
    if isinstance(tr, dict):
        loc = tr.get(lang)
        if isinstance(loc, dict) and loc.get(field_name):
            return str(loc[field_name])
    if field_name == "name":
        legacy = getattr(obj, "name_localized", None)
        if legacy:
            return legacy
    return base


def _money(value: Decimal | int | float | None) -> str:
    """Whole numbers print bare (`18`), otherwise two decimals (`18.50`)."""
    if value is None:
        return ""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        return f"{int(d)}"
    return f"{d:.2f}"


def _active_options(product_modifier) -> list:
    mod = product_modifier.modifier
    if mod is None:
        return []
    return sorted(
        [o for o in mod.options if o.is_active],
        key=lambda o: (o.display_order, o.name),
    )


def _pick_size_modifier(product: Product):
    """The required single-choice group whose options are the item's sizes.

    A required (`minimum_options > 0`), single-select (`maximum_options == 1`)
    group with two or more active options is how a coffee is priced by S/M/L or a
    cake by slice/whole. The lowest `display_order` such group wins; None when the
    item is a single-price line.
    """
    candidates = []
    for pm in product.product_modifiers:
        if (pm.minimum_options or 0) > 0 and (pm.maximum_options or 0) == 1:
            opts = _active_options(pm)
            if len(opts) >= 2:
                candidates.append((pm.display_order, pm, opts))
    if not candidates:
        return None, []
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1], candidates[0][2]


def _from_price(product: Product) -> tuple[Decimal, bool]:
    """(floor price, is_a_from_price) mirroring product_service._from_price.

    base_price + the cheapest active option of every required group; if that is
    zero, the cheapest non-zero active option on any group (the modifier-priced
    case). The bool is whether the number is a floor the customer chooses up from.
    """
    base = Decimal(str(product.base_price or 0))
    required_floor = Decimal("0")
    has_required_choice = False
    for pm in product.product_modifiers:
        if (pm.minimum_options or 0) > 0:
            opts = _active_options(pm)
            if opts:
                cheapest = min(Decimal(str(o.price or 0)) for o in opts)
                required_floor += cheapest
                if len(opts) >= 2:
                    has_required_choice = True
    floor = base + required_floor
    if floor > 0:
        return floor, has_required_choice
    # Modifier-priced: the options are the price, not a surcharge.
    priced = [
        Decimal(str(o.price or 0))
        for pm in product.product_modifiers
        for o in _active_options(pm)
        if Decimal(str(o.price or 0)) > 0
    ]
    if priced:
        return min(priced), True
    return base, False


def _build_item(product: Product, lang: str) -> MenuItem:
    name = _localized(product, "name", lang) or product.name
    description = _localized(product, "description", lang)
    image_url = product.image_urls[0] if product.image_urls else None

    size_pm, size_opts = _pick_size_modifier(product)
    variants: list[Variant] = []
    single_price: str | None = None
    prefix: str | None = None

    if size_pm is not None:
        base = Decimal(str(product.base_price or 0))
        for opt in size_opts:
            label = _localized(opt, "name", lang) or opt.name
            price = base + Decimal(str(opt.price or 0))
            variants.append(Variant(label=label, price=_money(price)))
    else:
        floor, is_from = _from_price(product)
        if floor > 0 or is_from:
            single_price = _money(floor)
            prefix = _t(lang, "from") if is_from else None

    return MenuItem(
        name=name,
        description=(description or None),
        image_url=image_url,
        image_data_uri=None,
        single_price=single_price,
        price_prefix=prefix,
        variants=variants,
        column_prices=None,
        calories=product.calories,
    )


def _apply_column_layout(section: MenuSection) -> None:
    """Give the section an aligned size-column grid when its items line up.

    The reference coffee menu lists S/M/L once as column headers and prints each
    drink's three prices under them. That only reads well when the drinks share a
    size set — so we take the most common variant-label tuple and use it as the
    columns iff enough of the section's variant items match it; the rest keep
    their inline chips.
    """
    variant_items = [it for it in section.items if it.variants]
    if len(variant_items) < 2:
        return
    tuples = Counter(tuple(v.label for v in it.variants) for it in variant_items)
    best, count = tuples.most_common(1)[0]
    if len(best) < 2 or count / len(variant_items) < _COLUMN_SHARE_THRESHOLD:
        return
    columns = list(best)
    section.columns = columns
    col_set = set(columns)
    # Align any item whose sizes are a *subset* of the columns — an item priced
    # only M/L in an S/M/L section fills those two cells and leaves S blank —
    # rather than only an exact match, which used to drop such an item onto the
    # single-price path and float its number past the grid.
    for it in section.items:
        if it.variants and {v.label for v in it.variants} <= col_set:
            by_label = {v.label: v.price for v in it.variants}
            it.column_prices = [by_label.get(lbl) for lbl in columns]


async def _load_products(db: AsyncSession, ids: list[uuid.UUID]) -> dict:
    if not ids:
        return {}
    stmt = (
        select(Product)
        .where(Product.id.in_(ids), Product.is_active.is_(True))
        .options(*product_service._product_load_options())
    )
    rows = (await db.execute(stmt)).scalars().unique().all()
    return {p.id: p for p in rows}


# Add-on / extras groups a customer reads last, not first — an "Extras" or
# "Add-ons" section leading a menu is the ordering complaint this guards against.
# Matched on the English group name so the answer is the same in either render.
_EXTRAS_KEYWORDS = (
    "extra",
    "add-on",
    "add on",
    "addon",
    "side",
    "sauce",
    "topping",
    "dip",
)


def _is_extras(english_name: str | None) -> bool:
    name = (english_name or "").lower()
    return any(k in name for k in _EXTRAS_KEYWORDS)


def _collect_sections(
    node: dict, products: dict, lang: str
) -> list[tuple[str, MenuSection]]:
    """Depth-first: every active group that holds products becomes a section.

    Group nesting flattens into an ordered list of sections titled by the group
    name (a branch tree can nest; the integrator tree is category→item). A group
    that only holds child groups contributes no section of its own — its children
    do — so there are no empty headers. Each section is paired with its English
    group name so the caller can order sensibly across languages.
    """
    result: list[tuple[str, MenuSection]] = []
    items: list[MenuItem] = []
    excluded = set(node.get("pdf_excluded_product_ids", []))
    for pid in node.get("product_ids", []):
        if pid in excluded:
            continue
        product = products.get(pid)
        if product is not None:
            items.append(_build_item(product, lang))
    if items:
        title = node["name_localized"] or node["name"] if lang == "ar" else node["name"]
        section = MenuSection(
            title=title or node["name"],
            icon_key=icon_key_for(node["name"]),
            items=items,
            columns=None,
        )
        _apply_column_layout(section)
        result.append((node["name"], section))
    for child in node.get("children", []):
        result.extend(_collect_sections(child, products, lang))
    return result


def _order_sections(pairs: list[tuple[str, MenuSection]]) -> list[MenuSection]:
    """Keep the operator's menu order, but sink extras/add-on groups to the end.

    The tree already arrives in `display_order` (the order the operator arranged
    on the register). We only make one customer-facing adjustment: a stable sort
    that moves add-on groups after the real menu, so a printed menu never opens on
    "Extras". Everything else holds its arranged order.
    """
    ordered = sorted(
        enumerate(pairs), key=lambda ip: (1 if _is_extras(ip[1][0]) else 0, ip[0])
    )
    return [section for _, (_, section) in ordered]


async def _resolve_brand(db: AsyncSession, root: MenuGroup):
    """(brand_name, logo_url, theme) for the menu's masthead.

    Branch (counter) menus take the legal entity that branch's counter channel
    trades under; the integrator menu has no branch and falls to the registered
    default. Either way the entity supplies the name, logo and palette.
    """
    entity = await tax_identity_service.resolve(
        db, branch_id=root.branch_id, source="cashier"
    )
    if entity is not None:
        return (
            entity.brand_name,
            entity.logo_url,
            theme_for_entity_reference(entity.reference),
        )
    return "Menu", None, theme_for_entity_reference(None)


def _whatsapp(branch: Branch | None) -> tuple[str | None, str | None]:
    if branch is None or not branch.phone:
        return None, None
    digits = "".join(ch for ch in branch.phone if ch.isdigit())
    if not digits:
        return branch.phone, None
    return branch.phone, f"https://wa.me/{digits}"


async def build_menu_document(
    db: AsyncSession, root: MenuGroup, *, lang: str
) -> MenuDocument:
    """Build the full view model for a menu-group root in `lang` ('en'|'ar')."""
    lang = "ar" if lang == "ar" else "en"

    tree = await menu_group_service.list_tree(
        db,
        include_inactive=False,
        branch_id=root.branch_id,
        root_kind=root.root_kind,
    )
    root_node = next((n for n in tree if n["id"] == root.id), None)
    if root_node is None:
        root_node = {
            "name": root.name,
            "name_localized": root.name_localized,
            "product_ids": [],
            "children": [],
        }

    all_ids = _all_product_ids(root_node)
    products = await _load_products(db, all_ids)
    sections = _order_sections(_collect_sections(root_node, products, lang))

    brand_name, logo_url, theme = await _resolve_brand(db, root)

    branch: Branch | None = None
    if root.branch_id is not None:
        branch = await db.get(Branch, root.branch_id)
    wa_number, wa_url = _whatsapp(branch)
    branch_name = branch.name_for(lang) if branch is not None else None

    return MenuDocument(
        lang=lang,
        direction="rtl" if lang == "ar" else "ltr",
        theme=theme,
        brand_name=brand_name,
        logo_url=logo_url,
        logo_data_uri=None,
        title=_t(lang, "menu"),
        branch_name=branch_name,
        whatsapp_number=wa_number,
        whatsapp_url=wa_url,
        qr_svg=None,
        sections=sections,
    )


def _all_product_ids(node: dict) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = list(node.get("product_ids", []))
    for child in node.get("children", []):
        ids.extend(_all_product_ids(child))
    # De-dup preserving order (a product can sit in more than one group).
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
