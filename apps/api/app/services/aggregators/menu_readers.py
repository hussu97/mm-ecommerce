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
#: bundle's own day labels). The others need their hours endpoint captured the
#: same way before they can be trusted to open/close a branch on the right day.
_HOURS_READERS: dict[str, Any] = {
    "careem": _read_careem_hours,
    "noon": _read_noon_hours,
    "deliveroo": _read_deliveroo_hours,
}
