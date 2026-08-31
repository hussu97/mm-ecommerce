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
- **Careem** — wired to the real catalog endpoints, replayed through the bearer
  session; its exact response field names get one confirmation pass at enablement
  (the parser is defensive until then).
- **Keeta / Talabat / Noon / Deliveroo** — same session-replay pattern; to add.
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
# Wired to the real partner-portal endpoints (confirmed live 2026-08-31), replayed
# through the same bearer session the sales ingest uses. The exact response FIELD
# NAMES could not be captured from the browser (the portal gates the JSON), so the
# parser is defensive — it tries the common Careem shapes — and gets one field
# confirmation pass at enablement against a live VM session. Gated off until then.


def _first(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "products", "items", "content", "result", "catalogs"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def parse_careem_catalog(
    catalogs: Any, products_by_category: dict[str, Any]
) -> NormalizedMenu:
    """Careem catalogs + per-category products → a channel-neutral menu.

    Defensive field mapping (name/price/available under several possible keys),
    confirmed against a live response at enablement.
    """
    cats: list[NormalizedCategory] = []
    for cat in _rows(catalogs):
        cat_id = str(_first(cat, "id", "categoryId", "catalogId", default=""))
        cat_name = _first(cat, "name", "title", "nameLocalized", default="")
        items = []
        for p in _rows(products_by_category.get(cat_id)):
            price = _first(p, "price", "basePrice", "priceInfo")
            if isinstance(price, dict):
                price = _first(price, "price", "amount", "value")
            avail = _first(p, "available", "isAvailable", "active", default=True)
            items.append(
                NormalizedItem(
                    name=_first(p, "name", "itemName", "title", default=""),
                    external_id=str(_first(p, "id", "itemId", default="")) or None,
                    price=Decimal(str(price)) if price is not None else None,
                    is_available=bool(avail),
                )
            )
        cats.append(
            NormalizedCategory(cat_name, external_id=cat_id or None, items=items)
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
    products_by_cat: dict[str, Any] = {}
    for cat in _rows(catalogs):
        cid = str(_first(cat, "id", "categoryId", "catalogId", default=""))
        if cid:
            products_by_cat[cid] = await cp.provider.list_catalog_products(
                session, company, brand, outlet, cid
            )
    return parse_careem_catalog(catalogs, products_by_cat)


# ── Reader registries ─────────────────────────────────────────────────────────
# A new reader is a single entry here plus its `async def _read_<target>_...`.
# Foodics reads the Grubtech price tag (real, verified); Careem replays its bearer
# session against the real catalog endpoints. Keeta/Talabat/Noon/Deliveroo follow
# the same session-replay pattern and land next.
_MENU_READERS: dict[str, Any] = {
    TARGET_FOODICS: _read_foodics_menu,
    "careem": _read_careem_menu,
}
_HOURS_READERS: dict[str, Any] = {}
