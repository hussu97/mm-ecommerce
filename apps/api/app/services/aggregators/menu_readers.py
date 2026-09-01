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
    NormalizedShift,
)
from app.services.providers.aggregator_base import AggregatorUnavailableError

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
# confirmed 2026-09-01). Read flow: catalog-catalogs -> catalog-categories/<id>
# ({subCategories}) -> catalog-products?categoryId=<cat> ({products:[...]}). A
# product's price is `defaultPrice`, availability is `status == "ACTIVE"`. Replayed
# through the same bearer session the sales ingest uses.


def _careem_items(products_payload: Any) -> list[NormalizedItem]:
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
        items.append(
            NormalizedItem(
                name=p["name"],
                external_id=str(p["id"]) if p.get("id") is not None else None,
                price=Decimal(str(price)) if price is not None else None,
                is_available=str(p.get("status", "ACTIVE")).upper() == "ACTIVE",
            )
        )
    return items


def parse_careem_catalog(
    categories: Any, products_by_category: dict[str, Any]
) -> NormalizedMenu:
    """Careem categories (the catalog's `subCategories`) + per-category products →
    a channel-neutral menu. Pure, unit-tested against the real shapes."""
    subs = (
        categories.get("subCategories") if isinstance(categories, dict) else categories
    ) or []
    cats: list[NormalizedCategory] = []
    for cat in subs:
        cid = str(cat.get("id")) if cat.get("id") is not None else ""
        cats.append(
            NormalizedCategory(
                cat.get("name", ""),
                external_id=cid or None,
                items=_careem_items(products_by_category.get(cid)),
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
        if cid:
            products_by_cat[cid] = await cp.provider.list_catalog_products(
                session, company, brand, outlet, cid
            )
    return parse_careem_catalog(categories, products_by_cat)


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


def _talabat_items(products: Any) -> list[NormalizedItem]:
    items: list[NormalizedItem] = []
    for p in products if isinstance(products, list) else []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        avail = p.get("availability")
        available = avail.get("available") if isinstance(avail, dict) else avail
        items.append(
            NormalizedItem(
                name=p["name"],
                external_id=str(p["id"]) if p.get("id") is not None else None,
                description=p.get("description"),
                price=Decimal(str(p["unitPrice"]))
                if p.get("unitPrice") is not None
                else None,
                is_available=bool(p.get("active", True))
                and bool(True if available is None else available),
            )
        )
    return items


def parse_talabat_catalog(
    catalogs: Any, products_by_category: dict[str, Any]
) -> NormalizedMenu:
    """Talabat catalogs (categories inline) + per-category products → menu. Pure,
    unit-tested against the real shapes."""
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
                    items=_talabat_items(products_by_category.get(cid)),
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
    for catalog in catalog_list:
        catalog_id = str(catalog["id"])
        for cat in catalog.get("categories", []) or []:
            cid = str(cat.get("id")) if cat.get("id") is not None else ""
            if cid:
                products_by_cat[cid] = await tp.provider.list_category_products(
                    session, vendor, catalog_id, cid
                )
    return parse_talabat_catalog(catalogs, products_by_cat)


# ── Noon RMS menu reader ──────────────────────────────────────────────────────
# Verified live from the VM session (2026-09-01). GET /menu/list -> the menus;
# POST /menu/details {menuCode} -> {items:[{itemCode,nameEn,price,isActive,isOos,
# categoryCode}], categories:[{categoryCode,nameEn,items:[itemCode]}]}. The
# "Ext. grubtech" menus are Foodics-fed; the MM-managed one is read here. Availability
# = isActive AND NOT isOos. Same RMS session/headers the finance ingest uses.


def parse_noon_menu(details: Any) -> NormalizedMenu:
    """Noon `/menu/details` data → a channel-neutral menu (pure, unit-tested).

    Categories reference items by `itemCode`; the item objects live in `items`.
    """
    data = details.get("data") if isinstance(details, dict) else details
    data = data or {}
    by_code = {
        it.get("itemCode"): it
        for it in (data.get("items") or [])
        if isinstance(it, dict)
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
                    external_id=str(it.get("itemCode")) if it.get("itemCode") else None,
                    external_ref=it.get("posSku"),
                    description=it.get("descEn"),
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=bool(it.get("isActive", True))
                    and not bool(it.get("isOos", False)),
                )
            )
        cats.append(
            NormalizedCategory(
                cat.get("nameEn", ""),
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


# ── Reader registries ─────────────────────────────────────────────────────────
# A new reader is a single entry here plus its `async def _read_<target>_...`.
# Foodics (Grubtech price tag), Careem (catalog REST), Talabat (DeliveryHero
# vendor-api) and Noon (RMS /menu/details) are all verified live from the real
# session. Keeta (H5guard) menu API can't be reached even server-side, and
# Deliveroo's menu is behind a separate Menus login — both need a headed capture.
_MENU_READERS: dict[str, Any] = {
    TARGET_FOODICS: _read_foodics_menu,
    "careem": _read_careem_menu,
    "talabat": _read_talabat_menu,
    "noon": _read_noon_menu,
}
#: Hours readers. Careem verified live (day origin confirmed against the portal
#: bundle's own day labels). The others need their hours endpoint captured the
#: same way before they can be trusted to open/close a branch on the right day.
_HOURS_READERS: dict[str, Any] = {
    "careem": _read_careem_hours,
}
