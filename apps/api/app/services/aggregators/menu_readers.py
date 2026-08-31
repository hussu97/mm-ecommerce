"""Read one integrator's live menu / hours into the channel-neutral shape.

The read side reuses the existing session/browser plumbing rather than reinventing
it: a marketplace reader loads the encrypted `aggregator_session` and replays it
the way the ingest providers do (`aggregator_base.request_json` — TLS-impersonated,
cookie-jar); the Foodics reader uses the console session the `foodics_provider`
already logs in with, and reads the **`Grubtech` group** (membership) + **price
tag** (the aggregator price) that the audit identified as the real integrated-branch
menu. Hours come from each portal's own schedule editor.

**Phase 1 status.** The dispatch, the normalized contract and the source mapping are
in place; the per-portal fetchers are **gated stubs** — `refresh_target` only calls
them when `CATALOG_SYNC_READ_ENABLED` is on, and until each is implemented it raises
`AggregatorUnavailableError`, which the sweep records as a snapshot `error` without
crashing. The drift pipeline runs off whatever snapshots exist, so it is fully
exercisable (see the tests) before a single live reader ships. Implement one reader
at a time here, newest-value-first (Foodics `Grubtech`, then the self-service
channels Keeta/Careem, then the anti-bot ones), each behind the same flag.
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


# ── Reader registries ─────────────────────────────────────────────────────────
# A new reader is a single entry here plus its `async def _read_<target>_...`.
# Foodics reads the Grubtech price tag (real, above); the marketplace readers
# replay the stored session through `aggregator_base` and land next.
_MENU_READERS: dict[str, Any] = {
    TARGET_FOODICS: _read_foodics_menu,
}
_HOURS_READERS: dict[str, Any] = {}
