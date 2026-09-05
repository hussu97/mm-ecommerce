"""Read one integrator's live menu / hours into the channel-neutral shape.

The read side reuses the existing session/browser plumbing rather than reinventing
it: a marketplace reader loads the encrypted `aggregator_session` and replays it
the way the ingest providers do (`aggregator_base.request_json` — TLS-impersonated,
cookie-jar); the Foodics reader uses the console session the `foodics_provider`
already logs in with, and reads the **`Grubtech` group** (membership) + **price
tag** (the aggregator price) that the audit identified as the real integrated-branch
menu. Hours come from each portal's own schedule editor.

**Status.** `refresh_target` only calls a reader when `CATALOG_SYNC_READ_ENABLED`
is on; a target with no reader (or a read that fails) records a snapshot `error`
without crashing, and the drift pipeline runs off whatever snapshots exist. Live
readers land newest-value-first:
- **Foodics** — DONE, verified against the live console API and the real 46-row
  Grubtech price tag (the aggregator menu for the two integrated branches).
- **Careem** — DONE, verified against the live catalog API (catalog-catalogs →
  catalog-categories → catalog-products; price = `defaultPrice`, availability =
  `status == "ACTIVE"`), replayed through the bearer session.
- **Talabat** — DONE, verified live from the VM session against the DeliveryHero
  vendor-api (price = `unitPrice`, availability = `availability.available` & `active`).
- **Noon** — DONE, verified live from the VM RMS session (`/menu/list` +
  `/menu/details`; price = `price`, availability = `isActive AND NOT isOos`).
- **Deliveroo** — menu is behind a SEPARATE Menus-editor login (the sales session's
  hub `token` does not reach `rs-hub`; "Edit menu" 302s to `/login`). Needs that
  second session captured before a reader can run — verified, not assumed.
- **Keeta** — menu API requires an in-browser H5guard (`mtgsig`) signature per
  request, so the stored session cookie cannot call it server-side. Browser-only;
  a headed capture is the only path. Verified from the portal's own shell JS.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog_sync import (
    SOURCE_BROWSER,
    SOURCE_FOODICS_API,
    SOURCE_HTTP,
    TARGET_FOODICS,
)
from app.services.aggregators.menu_normalized import (
    NormalizedCategory,
    NormalizedHours,
    NormalizedItem,
    NormalizedMenu,
    NormalizedModifierGroup,
    NormalizedOption,
    NormalizedShift,
)
from app.services.providers.aggregator_base import AggregatorUnavailableError

logger = logging.getLogger(__name__)

#: How each target's read is obtained, stamped onto the snapshot. The
#: TLS-impersonated channels answer over http; noon/talabat menu pages may need the
#: headed browser; Foodics answers on its console API.
_SOURCE_BY_TARGET: dict[str, str] = {
    "careem": SOURCE_HTTP,
    "keeta": SOURCE_HTTP,
    "deliveroo": SOURCE_BROWSER,
    "talabat": SOURCE_BROWSER,
    "noon": SOURCE_BROWSER,
    TARGET_FOODICS: SOURCE_FOODICS_API,
}


def source_for(target: str) -> str:
    return _SOURCE_BY_TARGET.get(target, SOURCE_HTTP)


async def fetch_menu(
    db: AsyncSession, *, target: str, branch_id: Any
) -> NormalizedMenu:
    """Read one outlet's live menu. Raises until the per-target reader ships."""
    reader = _MENU_READERS.get(target)
    if reader is None:
        raise AggregatorUnavailableError(
            f"live menu reader for {target!r} is not implemented yet "
            "(Phase 1 drift runs off stored snapshots)"
        )
    return await reader(db, branch_id)


async def fetch_hours(
    db: AsyncSession, *, target: str, branch_id: Any
) -> NormalizedHours:
    """Read one outlet's live hours. Raises until the per-target reader ships."""
    reader = _HOURS_READERS.get(target)
    if reader is None:
        raise AggregatorUnavailableError(
            f"live hours reader for {target!r} is not implemented yet "
            "(Phase 1 drift runs off stored snapshots)"
        )
    return await reader(db, branch_id)


# ── Foodics Grubtech reader (integrated branches) ─────────────────────────────
# The aggregator menu for Sharjah + Barsha IS the Foodics "Grubtech" price tag
# (verified live 2026-08-31): its products carry the aggregator price in
# `pivot.price`, its modifier options the variant prices. This reader is real —
# it replays the console session the `foodics_provider` already logs in with.


def _price_of(row: dict, field: str = "pivot") -> Decimal | None:
    """The aggregator price for a price-tag row: `pivot.price` (falls back to the
    row's own `price`). None only when neither is present."""
    pivot = row.get(field)
    val = pivot.get("price") if isinstance(pivot, dict) else None
    if val is None:
        val = row.get("price")
    return None if val is None else Decimal(str(val))


def parse_grubtech_price_tag(products: list[dict]) -> NormalizedMenu:
    """The price tag's products → a channel-neutral menu (pure, unit-tested).

    Flat single category — item identity is the name and the diff matches
    globally, so category grouping (a Foodics-internal concern) is not needed
    here. Price is the aggregator price (`pivot.price`); availability is
    `is_active`.
    """
    items = [
        NormalizedItem(
            name=p.get("name", ""),
            external_id=str(p.get("id")) if p.get("id") is not None else None,
            external_ref=p.get("sku"),
            price=_price_of(p),
            is_available=bool(p.get("is_active", True)),
        )
        for p in products
        if p.get("name")
    ]
    return NormalizedMenu(
        source=TARGET_FOODICS,
        categories=[NormalizedCategory("Grubtech", items=items)],
    )


def price_tag_parity_violations(products: list[dict]) -> list[dict]:
    """Products whose aggregator price (`pivot.price`) ≠ the product's own price —
    the operator's strict-parity policy violated (Ramadan/Christmas uplifts today).
    Surfaced in the snapshot stats and the drift report."""
    out = []
    for p in products:
        own = p.get("price")
        pivot = p.get("pivot")
        tag = pivot.get("price") if isinstance(pivot, dict) else None
        if (
            own is not None
            and tag is not None
            and Decimal(str(own)) != Decimal(str(tag))
        ):
            out.append({"name": p.get("name"), "product_price": own, "tag_price": tag})
    return out


async def _read_foodics_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    from app.services.providers import foodics_provider as fp

    pt_id = fp.FOODICS_GRUBTECH_PRICE_TAG_ID
    products = await fp.provider.list_price_tag_products(pt_id)
    menu = parse_grubtech_price_tag(products)
    violations = price_tag_parity_violations(products)
    menu.truncation_note = (
        f"{len(violations)} price-parity violation(s)" if violations else None
    )
    return menu


# ── Careem catalog reader (non-Foodics outlets) ───────────────────────────────
# Verified against the live partner-portal API (captured 2026-08-31, fields
# confirmed 2026-09-01, modifiers + category endpoint 2026-09-05). Read flow:
# catalog-catalogs -> catalog-categories?catalogId=<id> ({subCategories}) ->
# catalog-products?categoryId=<cat> ({products:[...]}). A product's price is
# `defaultPrice`, availability is `status == "ACTIVE"`, and its `customizationGroups`
# are embedded in the list product (no detail call). Replayed through the same
# bearer session the sales ingest uses.


def _careem_localized(obj: Any, key: str = "name") -> str:
    """A Careem entity's English name: `name`, else `nameLocalized.en`, else ""."""
    if not isinstance(obj, dict):
        return ""
    return obj.get(key) or (obj.get(f"{key}Localized") or {}).get("en") or ""


def _careem_ar(obj: Any, key: str = "name") -> str | None:
    """A Careem entity's Arabic value from `<key>Localized.ar`, or None."""
    if not isinstance(obj, dict):
        return None
    return (obj.get(f"{key}Localized") or {}).get("ar") or None


def _careem_image(obj: Any) -> str | None:
    """First image URL on a Careem product (`images:[{url}]`), or None."""
    for img in (obj.get("images") or []) if isinstance(obj, dict) else []:
        url = (img or {}).get("url") if isinstance(img, dict) else None
        if url:
            return url
    return None


def careem_groups_from_list(group_list: Any) -> list[NormalizedModifierGroup]:
    """Parse a Careem customization-group list → NormalizedModifierGroups. Group
    min/max come from `attributes.selection`; each option carries an `id`, `price`
    and (only when the list was fetched with `options=true`) a name/nameLocalized.
    Works for both the empty-name groups embedded in the catalog-products list and
    the named groups from `catalog-customization-groups?...&options=true`."""
    groups: list[NormalizedModifierGroup] = []
    for g in group_list or []:
        if not isinstance(g, dict):
            continue
        sel = (g.get("attributes") or {}).get("selection") or {}
        options: list[NormalizedOption] = []
        for o in g.get("options") or []:
            if not isinstance(o, dict):
                continue
            price = o.get("price")
            options.append(
                NormalizedOption(
                    name=_careem_localized(o),
                    name_ar=_careem_ar(o),
                    external_ref=str(o["id"]) if o.get("id") is not None else None,
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=str(o.get("status", "ACTIVE")).upper() != "INACTIVE",
                )
            )
        groups.append(
            NormalizedModifierGroup(
                name=_careem_localized(g),
                name_ar=_careem_ar(g),
                external_ref=str(g["id"]) if g.get("id") is not None else None,
                min_options=sel.get("min"),
                max_options=sel.get("max"),
                options=options,
            )
        )
    return groups


def careem_modifier_groups(product: dict) -> list[NormalizedModifierGroup]:
    """The groups embedded in a product's catalog-products row (option names empty)."""
    return careem_groups_from_list(product.get("customizationGroups"))


def _careem_items(
    products_payload: Any, groups_by_pid: dict[str, Any] | None = None
) -> list[NormalizedItem]:
    groups_by_pid = groups_by_pid or {}
    products = (
        products_payload.get("products")
        if isinstance(products_payload, dict)
        else products_payload
    ) or []
    items: list[NormalizedItem] = []
    for p in products:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        price = p.get("defaultPrice")
        if price is None and isinstance(p.get("prices"), list) and p["prices"]:
            price = (p["prices"][0] or {}).get("price")
        pid = str(p["id"]) if p.get("id") is not None else None
        # Prefer the named/priced groups fetched with options=true; fall back to
        # the (empty-name) groups embedded in the products list.
        if pid is not None and pid in groups_by_pid:
            mgroups = careem_groups_from_list(groups_by_pid[pid])
        else:
            mgroups = careem_modifier_groups(p)
        items.append(
            NormalizedItem(
                name=p["name"],
                name_ar=_careem_ar(p),
                external_id=pid,
                description=_careem_localized(p, "description") or None,
                description_ar=_careem_ar(p, "description"),
                image_url=_careem_image(p),
                price=Decimal(str(price)) if price is not None else None,
                is_available=str(p.get("status", "ACTIVE")).upper() == "ACTIVE",
                modifier_groups=mgroups,
            )
        )
    return items


def parse_careem_catalog(
    categories: Any,
    products_by_category: dict[str, Any],
    groups_by_pid: dict[str, Any] | None = None,
) -> NormalizedMenu:
    """Careem categories (the catalog's `subCategories`) + per-category products →
    a channel-neutral menu. Pure, unit-tested against the real shapes.
    `groups_by_pid` (productId → the `options=true` customization-group list) gives
    the named/priced options; without it, embedded empty-name groups are used."""
    subs = (
        categories.get("subCategories") if isinstance(categories, dict) else categories
    ) or []
    cats: list[NormalizedCategory] = []
    for cat in subs:
        cid = str(cat.get("id")) if cat.get("id") is not None else ""
        cats.append(
            NormalizedCategory(
                cat.get("name", ""),
                name_ar=_careem_ar(cat),
                external_id=cid or None,
                items=_careem_items(products_by_category.get(cid), groups_by_pid),
            )
        )
    return NormalizedMenu(source="careem", categories=cats)


async def _careem_ids(db: AsyncSession, branch_id: Any) -> tuple[str, str, str]:
    from sqlalchemy import select

    from app.models.aggregator import AggregatorBranchMap

    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == "careem",
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not (
        row.external_company_id and row.external_brand_id and row.external_outlet_id
    ):
        raise AggregatorUnavailableError(
            f"no active careem outlet map for branch {branch_id}"
        )
    return row.external_company_id, row.external_brand_id, row.external_outlet_id


async def _read_careem_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    from app.services.aggregators import session_store
    from app.services.providers import careem_provider as cp

    session = await session_store.load(db, "careem")
    company, brand, outlet = await _careem_ids(db, branch_id)
    catalogs = await cp.provider.list_catalogs(session, company, brand, outlet)
    catalog_list = (
        catalogs if isinstance(catalogs, list) else (catalogs or {}).get("data", [])
    )
    if not catalog_list:
        raise AggregatorUnavailableError("careem returned no catalog")
    catalog_id = str(catalog_list[0]["id"])
    categories = await cp.provider.list_categories(
        session, company, brand, outlet, catalog_id
    )
    subs = (
        categories.get("subCategories") if isinstance(categories, dict) else categories
    ) or []
    products_by_cat: dict[str, Any] = {}
    for cat in subs:
        cid = str(cat.get("id")) if cat.get("id") is not None else ""
        if not cid:
            continue
        try:
            products_by_cat[cid] = await cp.provider.list_catalog_products(
                session, company, brand, outlet, cid
            )
        except AggregatorUnavailableError as exc:
            # A parent/promo category can 404 on `catalog-products` ("unable to
            # find entity") — verified live on the Barsha outlet. One such category
            # must not abort the whole outlet's menu read; it contributes no items.
            logger.warning("careem: category %s has no products (%s)", cid, exc)
            products_by_cat[cid] = {"products": []}
    # Careem ALWAYS embeds `customizationGroups: []` in the products list (verified
    # live 2026-09-05) even for products that have groups, so the only way to know a
    # product's groups — and the only way to get option NAMES — is the per-product
    # `catalog-customization-groups?...&options=true` call. Fetch it for every
    # product; a product with no groups just returns [].
    groups_by_pid: dict[str, Any] = {}
    for payload in products_by_cat.values():
        plist = payload.get("products") if isinstance(payload, dict) else payload
        for p in plist or []:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            pid = str(p.get("id"))
            try:
                fetched = await cp.provider.list_customization_groups(
                    session, company, brand, outlet, pid, with_options=True
                )
            except AggregatorUnavailableError as exc:
                logger.warning(
                    "careem: product %s customizations unavailable (%s)", pid, exc
                )
                continue
            if fetched:
                groups_by_pid[pid] = fetched
    return parse_careem_catalog(categories, products_by_cat, groups_by_pid)


# ── Careem hours reader ───────────────────────────────────────────────────────
# `food-outlet-operational-hours` (read live 2026-09-01) returns a 7-element list:
#   [{day:1..7, active:0|1, shifts:[{start_time:"HH:MM:SS", end_time:"HH:MM:SS"}]}]
# `active:0` = closed that weekday; split shifts are multiple entries. The day
# origin is Careem's own: verified against the Store Manager bundle's day labels
# (`day1label:"Sunday" … day7:"Saturday"`), so day 1 = Sunday … day 7 = Saturday,
# which is MM's `weekday` (0=Sunday…6=Saturday) shifted by one: weekday = day - 1.


def _hhmm(value: Any) -> str:
    """`"08:00:00"` → `"08:00"`. Tolerates a value already in HH:MM."""
    s = str(value or "")
    parts = s.split(":")
    return f"{parts[0]:0>2}:{parts[1]:0>2}" if len(parts) >= 2 else s


def parse_careem_hours(rows: Any) -> NormalizedHours:
    """Careem's weekly operational hours → the channel-neutral schedule."""
    shifts: list[NormalizedShift] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("active"):
            continue  # active:0 (or missing) = closed that day → no shifts
        day = row.get("day")
        if not isinstance(day, int) or not (1 <= day <= 7):
            continue
        weekday = day - 1  # Careem day 1=Sunday → MM weekday 0=Sunday
        for shift in row.get("shifts") or []:
            if not isinstance(shift, dict):
                continue
            opens, closes = shift.get("start_time"), shift.get("end_time")
            if opens and closes:
                shifts.append(NormalizedShift(weekday, _hhmm(opens), _hhmm(closes)))
    return NormalizedHours(source="careem", shifts=shifts)


async def _read_careem_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    from app.services.aggregators import session_store
    from app.services.providers import careem_provider as cp

    session = await session_store.load(db, "careem")
    company, brand, outlet = await _careem_ids(db, branch_id)
    rows = await cp.provider.get_operational_hours(session, company, brand, outlet)
    return parse_careem_hours(rows)


# ── Talabat catalog reader ────────────────────────────────────────────────────
# Verified live from the VM session (2026-09-01). The DeliveryHero vendor-api backs
# the menu console: /catalogs -> {catalogs:[{id,name,categories:[{id,name}]}]};
# /catalogs/<cid>/categories/<catid>/products -> [{name, unitPrice,
# availability:{available}, active, ...}]. Replayed through the sales session (which
# carries the DeliveryHero bearer); request_json's TLS impersonation passes PX.


def _talabat_availability(obj: Any) -> bool:
    avail = obj.get("availability") if isinstance(obj, dict) else None
    available = avail.get("available") if isinstance(avail, dict) else avail
    return bool(True if available is None else available)


def _talabat_ar(names: Any) -> str | None:
    """The Arabic value from a Talabat `names`/`descriptions` array
    (`[{"locale":"ar-AE","value":...}, ...]`), or None."""
    for n in names or []:
        if isinstance(n, dict) and str(n.get("locale", "")).startswith("ar"):
            return n.get("value") or None
    return None


def talabat_option_group(group: Any) -> NormalizedModifierGroup | None:
    """A Talabat product-option group (the mix-box "Options (Max N)" flavour
    picker) → NormalizedModifierGroup (verified live 2026-09-05). Name/AR come from
    `names`, min/max from `quantity`, and each option carries `names` (en+ar) and
    `unitPrice` (0 for flavours). Distinct from a SIZED_PRODUCT's sizes."""
    if not isinstance(group, dict) or not group.get("name"):
        return None
    q = group.get("quantity") if isinstance(group.get("quantity"), dict) else {}
    options: list[NormalizedOption] = []
    for o in group.get("options") or []:
        if not isinstance(o, dict) or not o.get("name"):
            continue
        price = o.get("unitPrice")
        options.append(
            NormalizedOption(
                name=o["name"],
                name_ar=_talabat_ar(o.get("names")),
                external_ref=str(o["id"]) if o.get("id") is not None else None,
                price=Decimal(str(price)) if price is not None else None,
                is_available=bool(o.get("active", True)) and _talabat_availability(o),
            )
        )
    if not options:
        return None
    return NormalizedModifierGroup(
        name=group["name"],
        name_ar=_talabat_ar(group.get("names")),
        external_ref=str(group["id"]) if group.get("id") is not None else None,
        min_options=q.get("minimum"),
        max_options=q.get("maximum"),
        options=options,
    )


def talabat_size_group(detail: Any) -> NormalizedModifierGroup | None:
    """A SIZED_PRODUCT's sizes → a single "Size" modifier group (verified live
    2026-09-05). The category-products list carries only `productOptionIds`; the
    named, priced sizes are in the product-detail's `nestedProducts` (each
    `type:"SIZE"`, e.g. 3/6/9 Pieces at 55/100/145). One size is chosen, so
    min=max=1. Returns None when the product has no sizes."""
    nested = detail.get("nestedProducts") if isinstance(detail, dict) else None
    options: list[NormalizedOption] = []
    for n in nested or []:
        if not isinstance(n, dict) or str(n.get("type")) != "SIZE" or not n.get("name"):
            continue
        price = n.get("unitPrice")
        options.append(
            NormalizedOption(
                name=n["name"],
                external_ref=str(n["id"]) if n.get("id") is not None else None,
                price=Decimal(str(price)) if price is not None else None,
                is_available=_talabat_availability(n),
            )
        )
    if not options:
        return None
    return NormalizedModifierGroup(
        name="Size", min_options=1, max_options=1, options=options
    )


def _talabat_items(
    products: Any,
    details_by_id: dict[str, Any] | None = None,
    options_by_id: dict[str, Any] | None = None,
) -> list[NormalizedItem]:
    details_by_id = details_by_id or {}
    options_by_id = options_by_id or {}
    items: list[NormalizedItem] = []
    for p in products if isinstance(products, list) else []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        # `imageUrls` is usually null; the real image is in `images`, a list of
        # `{"url", "width", "height"}` dicts — take the first url as a string.
        image_urls = p.get("imageUrls") or p.get("images") or []
        first_image = (
            image_urls[0] if isinstance(image_urls, list) and image_urls else None
        )
        image_url = (
            first_image.get("url") if isinstance(first_image, dict) else first_image
        )
        item = NormalizedItem(
            name=p["name"],
            external_id=str(p["id"]) if p.get("id") is not None else None,
            description=p.get("description"),
            image_url=image_url,
            price=Decimal(str(p["unitPrice"]))
            if p.get("unitPrice") is not None
            else None,
            is_available=bool(p.get("active", True)) and _talabat_availability(p),
        )
        detail = details_by_id.get(str(p.get("id")))
        if detail is not None:
            group = talabat_size_group(detail)
            if group is not None:
                item.modifier_groups.append(group)
        # Flavour groups the product references by id (the mix-box "Options (Max
        # N)" pickers) — resolved from the vendor's product-options list.
        for oid in p.get("productOptionIds") or []:
            grp = talabat_option_group(options_by_id.get(str(oid)))
            if grp is not None:
                item.modifier_groups.append(grp)
        items.append(item)
    return items


def parse_talabat_catalog(
    catalogs: Any,
    products_by_category: dict[str, Any],
    details_by_id: dict[str, Any] | None = None,
    options_by_id: dict[str, Any] | None = None,
) -> NormalizedMenu:
    """Talabat catalogs (categories inline) + per-category products → menu. Pure,
    unit-tested against the real shapes. `details_by_id` (productId → product-detail
    payload) carries the sizes for SIZED_PRODUCTs, attached as a "Size" group;
    `options_by_id` (optionGroupId → group) carries the flavour groups a product
    references by `productOptionIds`."""
    catalog_list = (
        catalogs.get("catalogs") if isinstance(catalogs, dict) else catalogs
    ) or []
    cats: list[NormalizedCategory] = []
    for catalog in catalog_list:
        for cat in catalog.get("categories", []) or []:
            cid = str(cat.get("id")) if cat.get("id") is not None else ""
            cats.append(
                NormalizedCategory(
                    cat.get("name", ""),
                    external_id=cid or None,
                    items=_talabat_items(
                        products_by_category.get(cid), details_by_id, options_by_id
                    ),
                )
            )
    return NormalizedMenu(source="talabat", categories=cats)


async def _talabat_vendor(db: AsyncSession, branch_id: Any) -> str:
    from sqlalchemy import select

    from app.models.aggregator import AggregatorBranchMap

    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == "talabat",
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.external_outlet_id:
        raise AggregatorUnavailableError(
            f"no active talabat outlet map for branch {branch_id}"
        )
    return row.external_outlet_id


async def _read_talabat_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    from app.services.aggregators import session_store
    from app.services.providers import talabat_provider as tp

    session = await session_store.load(db, "talabat")
    vendor = await _talabat_vendor(db, branch_id)
    catalogs = await tp.provider.list_catalogs(session, vendor)
    catalog_list = (
        catalogs.get("catalogs") if isinstance(catalogs, dict) else catalogs
    ) or []
    products_by_cat: dict[str, Any] = {}
    sized_ids: set[str] = set()
    for catalog in catalog_list:
        catalog_id = str(catalog["id"])
        for cat in catalog.get("categories", []) or []:
            cid = str(cat.get("id")) if cat.get("id") is not None else ""
            if not cid:
                continue
            prods = await tp.provider.list_category_products(
                session, vendor, catalog_id, cid
            )
            products_by_cat[cid] = prods
            for p in prods if isinstance(prods, list) else []:
                # A SIZED_PRODUCT's sizes are only in its product-detail call.
                if (
                    isinstance(p, dict)
                    and str(p.get("type")) == "SIZED_PRODUCT"
                    and p.get("id") is not None
                ):
                    sized_ids.add(str(p["id"]))
    details_by_id: dict[str, Any] = {}
    for pid in sized_ids:
        try:
            details_by_id[pid] = await tp.provider.get_product_detail(
                session, vendor, pid
            )
        except AggregatorUnavailableError as exc:
            # One product's detail failing must not lose the whole menu read; that
            # item simply carries no sizes this pass.
            logger.warning("talabat: product %s detail unavailable (%s)", pid, exc)
    # The vendor's flavour groups, once, indexed by id for productOptionIds lookup.
    options_by_id: dict[str, Any] = {}
    try:
        groups = await tp.provider.list_product_options(session, vendor)
        for g in (
            groups if isinstance(groups, list) else (groups.get("productOptions") or [])
        ):
            if isinstance(g, dict) and g.get("id") is not None:
                options_by_id[str(g["id"])] = g
    except AggregatorUnavailableError as exc:
        logger.warning("talabat: product-options unavailable (%s)", exc)
    return parse_talabat_catalog(
        catalogs, products_by_cat, details_by_id, options_by_id
    )


def _min_hhmm(value: Any) -> str:
    """Minutes-from-midnight (e.g. 495) → `"08:15"`. Wraps 1440 to 00:00."""
    try:
        m = int(value) % 1440
    except (TypeError, ValueError):
        return "00:00"
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_talabat_hours(raw: Any) -> NormalizedHours:
    """The DeliveryHero Vendor Time Service `calendars/DELIVERY` payload → the
    channel-neutral schedule. Uses the `Normal` calendar's `openingTimesByDay`;
    `from`/`to` are minutes-from-midnight and `day` is 0=Monday..6=Sunday
    (`firstDOW=0`), so `weekday = (day + 1) % 7` maps to MM's 0=Sunday. A day
    absent from the list is closed (e.g. Karama is shut Friday)."""
    calendars = raw.get("calendars") if isinstance(raw, dict) else None
    calendars = calendars or []
    normal = next(
        (c for c in calendars if isinstance(c, dict) and c.get("name") == "Normal"),
        calendars[0] if calendars else {},
    )
    by_day = (normal.get("schedule") or {}).get("openingTimesByDay") or []
    shifts: list[NormalizedShift] = []
    for entry in by_day:
        if not isinstance(entry, dict):
            continue
        day = entry.get("day")
        if not isinstance(day, int) or not (0 <= day <= 6):
            continue
        weekday = (day + 1) % 7  # DH 0=Mon..6=Sun → MM 0=Sun
        for window in entry.get("openingTimes") or []:
            if not isinstance(window, dict):
                continue
            opens, closes = window.get("from"), window.get("to")
            if opens is not None and closes is not None:
                shifts.append(
                    NormalizedShift(weekday, _min_hhmm(opens), _min_hhmm(closes))
                )
    shifts.sort(key=lambda s: (s.weekday, s.opens))
    return NormalizedHours(source="talabat", shifts=shifts)


async def _read_talabat_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    """Talabat opening hours, read live server-side from the DeliveryHero Vendor
    Time Service — the same TLS-impersonated session the menu/sales reads use, no
    headed browser (verified live 2026-09-02 for all 3 vendors)."""
    from app.services.aggregators import session_store
    from app.services.providers import talabat_provider as tp

    session = await session_store.load(db, "talabat")
    vendor = await _talabat_vendor(db, branch_id)
    raw = await tp.provider.get_delivery_calendars(session, vendor)
    return parse_talabat_hours(raw)


# ── Noon RMS menu reader ──────────────────────────────────────────────────────
# Verified live from the VM session (2026-09-01, modifiers 2026-09-05). GET
# /menu/list -> the menus; POST /menu/details {menuCode} -> {items:[{itemCode,
# nameEn,price,isActive,isOos,categoryCode,modifiers:[modifierCode]}], categories:
# [{categoryCode,nameEn,items:[itemCode]}], modifiers:[{modifierCode,nameEn,
# minTotalOptions,maxTotalOptions,options:[{itemCode,price}]}]}. An item's
# customization groups are referenced by code in `item.modifiers`; an option's name
# is the referenced item's nameEn (options are non-`main` items). The "Ext. grubtech"
# menus are Foodics-fed; the MM-managed one is read here. Availability = isActive AND
# NOT isOos. Same RMS session/headers the finance ingest uses.


def _noon_item_available(it: dict) -> bool:
    """Noon availability: `isActive AND NOT isOos` (used for items and options)."""
    return bool(it.get("isActive", True)) and not bool(it.get("isOos", False))


def _noon_modifier_groups(
    item: dict,
    groups_by_code: dict[str, dict],
    by_code: dict[str, dict],
) -> list[NormalizedModifierGroup]:
    """The item's customization groups → NormalizedModifierGroups (verified live
    2026-09-05): `item.modifiers` is a list of modifier codes; each resolves in
    `data.modifiers` to a group `{modifierCode, nameEn, minTotalOptions,
    maxTotalOptions, options:[{itemCode, price}]}`; an option's display name is the
    referenced item's `nameEn` (options are non-`main` items in the same `items`
    list), its price is the option's `price` override."""
    groups: list[NormalizedModifierGroup] = []
    for code in item.get("modifiers") or []:
        grp = groups_by_code.get(code)
        if not isinstance(grp, dict):
            continue
        options: list[NormalizedOption] = []
        for opt in grp.get("options") or []:
            if not isinstance(opt, dict):
                continue
            ref_item = by_code.get(opt.get("itemCode")) or {}
            name = ref_item.get("nameEn") or opt.get("nameEn")
            if not name:
                continue
            price = opt.get("price")
            options.append(
                NormalizedOption(
                    name=name,
                    name_ar=ref_item.get("nameAr") or opt.get("nameAr"),
                    external_ref=str(opt.get("itemCode"))
                    if opt.get("itemCode")
                    else None,
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=_noon_item_available(ref_item),
                )
            )
        groups.append(
            NormalizedModifierGroup(
                name=grp.get("nameEn", ""),
                name_ar=grp.get("nameAr"),
                external_ref=str(code) if code else None,
                min_options=grp.get("minTotalOptions"),
                max_options=grp.get("maxTotalOptions"),
                options=options,
            )
        )
    return groups


def parse_noon_menu(details: Any) -> NormalizedMenu:
    """Noon `/menu/details` data → a channel-neutral menu (pure, unit-tested).

    Categories reference items by `itemCode`; the item objects live in `items`.
    Variant-priced items (₿0 base) carry their real prices in a customization group
    referenced from `item.modifiers`, parsed here via `_noon_modifier_groups`.
    """
    data = details.get("data") if isinstance(details, dict) else details
    data = data or {}
    by_code = {
        it.get("itemCode"): it
        for it in (data.get("items") or [])
        if isinstance(it, dict)
    }
    groups_by_code = {
        g.get("modifierCode"): g
        for g in (data.get("modifiers") or [])
        if isinstance(g, dict) and g.get("modifierCode")
    }
    cats: list[NormalizedCategory] = []
    for cat in sorted(data.get("categories") or [], key=lambda c: c.get("position", 0)):
        items: list[NormalizedItem] = []
        for code in cat.get("items") or []:
            it = by_code.get(code)
            if not it or not it.get("nameEn"):
                continue
            price = it.get("price")
            items.append(
                NormalizedItem(
                    name=it["nameEn"],
                    name_ar=it.get("nameAr"),
                    external_id=str(it.get("itemCode")) if it.get("itemCode") else None,
                    external_ref=it.get("posSku"),
                    description=it.get("descEn"),
                    description_ar=it.get("descAr"),
                    image_url=it.get("image") or None,
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=_noon_item_available(it),
                    modifier_groups=_noon_modifier_groups(it, groups_by_code, by_code),
                )
            )
        cats.append(
            NormalizedCategory(
                cat.get("nameEn", ""),
                name_ar=cat.get("nameAr"),
                external_id=str(cat.get("categoryCode"))
                if cat.get("categoryCode")
                else None,
                items=items,
            )
        )
    return NormalizedMenu(source="noon", categories=cats)


async def _read_noon_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    from app.services.aggregators import session_store
    from app.services.providers import noon_provider as np

    session = await session_store.load(db, "noon")
    menus = await np.provider.list_menus(session)
    rows = (menus.get("data") if isinstance(menus, dict) else menus) or []
    # The MM-managed menu is the one that is NOT the Foodics-fed "Ext. grubtech".
    mm_menus = [m for m in rows if not str(m.get("menuName", "")).startswith("Ext.")]
    chosen = mm_menus or rows
    if not chosen:
        raise AggregatorUnavailableError("noon returned no menu")
    details = await np.provider.get_menu_details(session, chosen[0]["menuCode"])
    return parse_noon_menu(details)


# ── Noon hours reader ─────────────────────────────────────────────────────────
# `restaurant/outlet/details` → `data.schedule.periods`: {dayIdxKey: [[open,close]]}.
# Keys are day indices, comma-joined for a shared schedule ("0,1,2,3"). The
# response's own `periodsDesc` proves the origin: day 0=Mon … 6=Sun. MM weekday is
# 0=Sun…6=Sat, so MM weekday = (noon_day + 1) % 7. Verified live 2026-09-01.


def parse_noon_hours(details: Any) -> NormalizedHours:
    """Noon outlet-detail `schedule.periods` → the channel-neutral weekly schedule."""
    data = details.get("data") if isinstance(details, dict) else details
    schedule = (data or {}).get("schedule") if isinstance(data, dict) else None
    periods = (schedule or {}).get("periods") if isinstance(schedule, dict) else None
    shifts: list[NormalizedShift] = []
    if isinstance(periods, dict):
        for key, ranges in periods.items():
            # A key is one or more comma-joined noon day indices (0=Mon…6=Sun).
            days = []
            for part in str(key).split(","):
                part = part.strip()
                if part.isdigit() and 0 <= int(part) <= 6:
                    days.append((int(part) + 1) % 7)  # → MM weekday (0=Sun…6=Sat)
            for weekday in days:
                for rng in ranges or []:
                    if (
                        isinstance(rng, (list, tuple))
                        and len(rng) >= 2
                        and rng[0]
                        and rng[1]
                    ):
                        shifts.append(
                            NormalizedShift(weekday, _hhmm(rng[0]), _hhmm(rng[1]))
                        )
    shifts.sort(key=lambda s: (s.weekday, s.opens))
    return NormalizedHours(source="noon", shifts=shifts)


async def _noon_outlet_code(db: AsyncSession, branch_id: Any) -> str:
    from sqlalchemy import select

    from app.models.aggregator import AggregatorBranchMap

    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == "noon",
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.external_outlet_id:
        raise AggregatorUnavailableError(
            f"no active noon outlet map for branch {branch_id}"
        )
    return row.external_outlet_id


async def _read_noon_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    from app.services.aggregators import session_store
    from app.services.providers import noon_provider as np

    session = await session_store.load(db, "noon")
    outlet_code = await _noon_outlet_code(db, branch_id)
    details = await np.provider.get_outlet_details(session, outlet_code)
    return parse_noon_hours(details)


# ── Keeta menu (via the headed worker's snapshot) ─────────────────────────────
# Keeta signs every menu XHR with an in-browser `mtgsig` (H5guard), so the menu
# API cannot be called server-side — the headed worker fetches it and pushes the
# raw payload, which we parse here. Endpoints + shapes verified live from the
# portal 2026-09-01 (merchant.mykeeta.com/m/web/product):
#   POST /api/sailorProduct/shopCategory/r/listShopCategory {shopId}
#     -> {data:[{id, name, status, availableTimeDTO:{values:[...]}}]}
#   POST /api/sailorProduct/spu/r/listSpu {shopId, pageNum, pageSize}
#     -> {data:{spuList:[{id, name, status, shopCategoryIdList:[catId],
#               skuList:[{price:"0", currency:"AED"}]}]}}
# Price is the first SKU's price; sizes are separate SPUs (like Careem/Talabat).


def parse_keeta_menu(raw: Any) -> NormalizedMenu:
    """The worker's Keeta menu push (`{categories:[...], spus:[...]}`) → a menu.

    Pure + unit-tested against the real captured shapes. `status == 1` is
    available; a product's category is its first `shopCategoryIdList` entry; price
    is the first SKU's `price` in its `currency`."""
    if not isinstance(raw, dict):
        raw = {}
    cats_raw = raw.get("categories") or []
    spus_raw = raw.get("spus") or raw.get("spuList") or []
    cat_name = {
        str(c.get("id")): c.get("name", "")
        for c in cats_raw
        if isinstance(c, dict) and c.get("id") is not None
    }
    by_cat: dict[str, NormalizedCategory] = {}
    order: list[str] = []
    for spu in spus_raw:
        if not isinstance(spu, dict) or not spu.get("name"):
            continue
        cat_ids = spu.get("shopCategoryIdList") or []
        cid = str(cat_ids[0]) if cat_ids else ""
        cname = cat_name.get(cid, "Uncategorised")
        skus = spu.get("skuList") or []
        price = None
        if (
            skus
            and isinstance(skus[0], dict)
            and skus[0].get("price") not in (None, "")
        ):
            try:
                price = Decimal(str(skus[0]["price"]))
            except Exception:  # noqa: BLE001 — a bad price is "unknown", not a crash
                price = None
        item = NormalizedItem(
            name=spu["name"],
            external_id=str(spu["id"]) if spu.get("id") is not None else None,
            price=price,
            is_available=spu.get("status") == 1,
            category_ref=cid or None,
        )
        if cid not in by_cat:
            by_cat[cid] = NormalizedCategory(cname, external_id=cid or None)
            order.append(cid)
        by_cat[cid].items.append(item)
    return NormalizedMenu(source="keeta", categories=[by_cat[k] for k in order])


async def _read_keeta_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    """Parse the latest Keeta menu the headed worker pushed into the snapshot.

    Keeta cannot be read server-side (H5guard); the worker's `MENU` job fetches it
    and pushes the raw payload, stored as the keeta menu snapshot's `raw`. This
    reads that — so a Keeta refresh is 'reparse the last worker push', not a live
    call. Raises until the worker has pushed at least once."""
    from sqlalchemy import select

    from app.models.catalog_sync import SNAPSHOT_MENU, AggregatorMenuSnapshot

    snap = (
        await db.execute(
            select(AggregatorMenuSnapshot)
            .where(
                AggregatorMenuSnapshot.target == "keeta",
                AggregatorMenuSnapshot.kind == SNAPSHOT_MENU,
            )
            .order_by(AggregatorMenuSnapshot.fetched_at.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None or not snap.raw:
        raise AggregatorUnavailableError(
            "no keeta menu pushed yet — the headed worker's MENU job must run first"
        )
    return parse_keeta_menu(snap.raw)


# ── Keeta hours reader ────────────────────────────────────────────────────────
# The worker's `fetch_keeta_today_hours` returns the SCM summary `shopList`: per shop
# `{shopId, businessStatus, todayBusinessHours:[{startTime,endTime}]}` where the times
# are SECONDS-from-midnight (verified live 2026-09-01). This is TODAY's window only —
# Keeta does not expose a weekly schedule on this account — so the reader yields shifts
# for the given `weekday` (MM 0=Sun…6=Sat) rather than a full week. `businessStatus!=1`
# (temporarily closed) yields no shift for the day.


def _seconds_to_hhmm(seconds: Any) -> str:
    """`28800` → `"08:00"` (seconds-from-midnight, clamped to a 24h clock)."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return ""
    total = max(0, min(total, 24 * 3600))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}"


def parse_keeta_today_hours(shop: Any, weekday: int) -> NormalizedHours:
    """One Keeta shop's TODAY window → the channel-neutral schedule for `weekday`.

    `shop` is one element of the SCM summary `shopList`. Only today's window is
    exposed, so the result carries shifts for `weekday` alone."""
    shifts: list[NormalizedShift] = []
    if isinstance(shop, dict) and shop.get("businessStatus") == 1:
        for win in shop.get("todayBusinessHours") or []:
            if not isinstance(win, dict):
                continue
            opens = _seconds_to_hhmm(win.get("startTime"))
            closes = _seconds_to_hhmm(win.get("endTime"))
            if opens and closes:
                shifts.append(NormalizedShift(weekday, opens, closes))
    return NormalizedHours(source="keeta", shifts=shifts)


async def _keeta_outlet_id(db: AsyncSession, branch_id: Any) -> str:
    from sqlalchemy import select

    from app.models.aggregator import AggregatorBranchMap

    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == "keeta",
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.external_outlet_id:
        raise AggregatorUnavailableError(
            f"no keeta outlet mapped for branch {branch_id}"
        )
    return row.external_outlet_id


async def _read_keeta_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    """Parse the latest Keeta today-window the headed worker pushed.

    Keeta does not expose a weekly schedule on this account — only today's
    window — so this yields shifts for today's MM weekday. Raises until the
    worker has pushed at least once.
    """
    from sqlalchemy import select

    from app.core import trading_hours
    from app.models.catalog_sync import SNAPSHOT_HOURS, AggregatorMenuSnapshot

    snap = (
        await db.execute(
            select(AggregatorMenuSnapshot)
            .where(
                AggregatorMenuSnapshot.target == "keeta",
                AggregatorMenuSnapshot.kind == SNAPSHOT_HOURS,
            )
            .order_by(AggregatorMenuSnapshot.fetched_at.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None or not snap.raw:
        raise AggregatorUnavailableError(
            "no keeta hours pushed yet — the headed worker's HOURS job must run first"
        )
    raw = snap.raw
    shops = raw.get("shopList") if isinstance(raw, dict) else raw
    if not isinstance(shops, list):
        shops = [raw] if isinstance(raw, dict) else []
    outlet = await _keeta_outlet_id(db, branch_id)
    shop = next(
        (
            s
            for s in shops
            if isinstance(s, dict)
            and str(s.get("shopId") or s.get("id") or "") == outlet
        ),
        None,
    )
    if shop is None:
        raise AggregatorUnavailableError(
            f"keeta hours snapshot has no shop {outlet} for branch {branch_id}"
        )
    python_weekday = trading_hours.local(datetime.now(trading_hours.TZ)).weekday()
    mm_weekday = (python_weekday + 1) % 7
    return parse_keeta_today_hours(shop, mm_weekday)


# ── Deliveroo menu + hours parsers ────────────────────────────────────────────
# Discovered live 2026-09-01 from the hub session (which auto-exchanges a webrom
# token — no separate credentials): the menu is
# `GET api.webrom.restaurants.deliveroo.com/rom/{rst}/menu` →
#   {categories:[{name, items:[{id, name, description, price, status}]}]}
# and hours are `GET partner-hub.deliveroo.com/api/restaurants/{rst}/opening_hours` →
#   {hours:[{day_of_week, local_start_time, local_end_time}]}.
# Money: `price` is in MAJOR AED units (cake slice 35, 9-piece box 145 — matched to
# the real MM prices), so no minor-unit scaling. Availability: status == "ACTIVE".
# Day origin: `day_of_week` 0=Sunday…6=Saturday (the Fri=12:00 / Sat=14:00 / Sun=17:00
# progression only fits 0=Sunday) — which IS MM's weekday, so no shift.


def parse_deliveroo_menu(raw: Any) -> NormalizedMenu:
    """The webrom `/rom/{rst}/menu` payload → the channel-neutral menu. Pure,
    unit-tested against the real captured shape."""
    cats: list[NormalizedCategory] = []
    categories = raw.get("categories") if isinstance(raw, dict) else raw
    for cat in categories or []:
        if not isinstance(cat, dict):
            continue
        items: list[NormalizedItem] = []
        for it in cat.get("items") or []:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            price = it.get("price")
            items.append(
                NormalizedItem(
                    name=it["name"],
                    external_id=str(it["id"]) if it.get("id") is not None else None,
                    description=it.get("description"),
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=str(it.get("status", "ACTIVE")).upper() == "ACTIVE",
                )
            )
        cats.append(
            NormalizedCategory(
                cat.get("name", ""),
                external_id=str(cat["id"]) if cat.get("id") is not None else None,
                items=items,
            )
        )
    return NormalizedMenu(source="deliveroo", categories=cats)


def parse_deliveroo_hours(raw: Any) -> NormalizedHours:
    """The `opening_hours` payload → the channel-neutral schedule. `day_of_week`
    0=Sunday…6=Saturday is MM's weekday exactly (no shift)."""
    shifts: list[NormalizedShift] = []
    rows = raw.get("hours") if isinstance(raw, dict) else raw
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        day = row.get("day_of_week")
        if not isinstance(day, int) or not (0 <= day <= 6):
            continue
        opens, closes = row.get("local_start_time"), row.get("local_end_time")
        if opens and closes:
            shifts.append(NormalizedShift(day, _hhmm(opens), _hhmm(closes)))
    shifts.sort(key=lambda s: (s.weekday, s.opens))
    return NormalizedHours(source="deliveroo", shifts=shifts)


async def _deliveroo_snapshot(db: AsyncSession, kind: str) -> Any:
    """The latest Deliveroo snapshot raw of `kind`, or raise until the worker pushed."""
    from sqlalchemy import select

    from app.models.catalog_sync import AggregatorMenuSnapshot

    snap = (
        await db.execute(
            select(AggregatorMenuSnapshot)
            .where(
                AggregatorMenuSnapshot.target == "deliveroo",
                AggregatorMenuSnapshot.kind == kind,
            )
            .order_by(AggregatorMenuSnapshot.fetched_at.desc().nullslast())
            .limit(1)
        )
    ).scalar_one_or_none()
    if snap is None or not snap.raw:
        raise AggregatorUnavailableError(
            f"no deliveroo {kind} pushed yet — the headed worker's job must run first"
        )
    return snap.raw


async def _read_deliveroo_menu(db: AsyncSession, branch_id: Any) -> NormalizedMenu:
    """Parse the latest Deliveroo menu the headed worker pushed (webrom is behind
    Cloudflare + a webrom token, so it can't be read server-side)."""
    return parse_deliveroo_menu(await _deliveroo_snapshot(db, "menu"))


async def _deliveroo_outlet_id(db: AsyncSession, branch_id: Any) -> str:
    """The Deliveroo restaurant id mapped to this MM branch."""
    from sqlalchemy import select

    from app.models.aggregator import AggregatorBranchMap

    row = (
        await db.execute(
            select(AggregatorBranchMap).where(
                AggregatorBranchMap.channel == "deliveroo",
                AggregatorBranchMap.branch_id == branch_id,
                AggregatorBranchMap.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.external_outlet_id:
        raise AggregatorUnavailableError(
            f"no deliveroo outlet mapped for branch {branch_id}"
        )
    return row.external_outlet_id


async def _read_deliveroo_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    """Deliveroo opening hours, read live server-side (not the worker push).

    `GET /api/restaurants/{id}/opening_hours` replays fine over the provider's
    TLS-impersonating transport with the session's cf_clearance — the SPA just
    stopped firing it on page load after the Partner Hub's /api-gw/ restructure, so
    a direct fetch is both correct and more robust than the passive page-capture."""
    from app.services.aggregators import session_store
    from app.services.providers import deliveroo_provider as dp

    session = await session_store.load(db, "deliveroo")
    # prepare_session RETURNS the prepared session — a NEW object when it refreshes
    # or re-logs-in a stale token (and it augments org_id / outlet ids onto it). Use
    # the return value: the passed-in `session` stays stale, which 401s on an expired
    # token and loses the org_id augmentation.
    session = await dp.provider.prepare_session(db, session)
    if session is None:
        raise AggregatorUnavailableError("no deliveroo session")
    outlet = await _deliveroo_outlet_id(db, branch_id)
    raw = await dp.provider.get_opening_hours(session, outlet)
    return parse_deliveroo_hours(raw)


# ── Reader registries ─────────────────────────────────────────────────────────
# A new reader is a single entry here plus its `async def _read_<target>_...`.
# Foodics (Grubtech price tag), Careem (catalog REST), Talabat (DeliveryHero
# vendor-api) and Noon (RMS /menu/details) are read live from the real session.
# Keeta and Deliveroo are parsed from the headed worker's push (anti-bot blocks a
# server call) — both endpoints + parsers verified live 2026-09-01.
_MENU_READERS: dict[str, Any] = {
    TARGET_FOODICS: _read_foodics_menu,
    "careem": _read_careem_menu,
    "talabat": _read_talabat_menu,
    "noon": _read_noon_menu,
    "keeta": _read_keeta_menu,
    "deliveroo": _read_deliveroo_menu,
}
#: Hours readers. Careem verified live (day origin confirmed against the portal
#: bundle's own day labels). Noon/talabat/deliveroo endpoints are the same
#: server-side reads. Keeta is today-only, parsed from the headed worker push.
_HOURS_READERS: dict[str, Any] = {
    "careem": _read_careem_hours,
    "noon": _read_noon_hours,
    "deliveroo": _read_deliveroo_hours,
    "talabat": _read_talabat_hours,
    "keeta": _read_keeta_hours,
}
