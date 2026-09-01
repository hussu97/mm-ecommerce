"""Resolve a fetched aggregator menu into `external_item_map` rows — the mapping.

The ingest already proposes item→product rows from scraped order lines (exact-name
guesses, left unapproved for review). This does the same from a *full menu read*
(so it also sees items nobody has ordered yet) and, crucially, resolves the two
things the order feed cannot:

- **options** — matched by `(name, price)`, because an option name alone ("9
  Pieces") is shared across products at different prices, and the menu read carries
  both;
- **the aggregator's own id** for each entity, so a later write knows what to edit.

It reuses `external_item_map` (not a parallel table) and the same approval gate:
an exact name (or name+price) match is recorded and — when `approve_exact` — marked
approved + `manual` (a menu read is authoritative, unlike an order-line guess); a
fuzzy or absent match is recorded unapproved for the item-mappings console. Idempotent
per identity. Nothing here commits — the caller's request/sweep does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.external_item_map import (
    KIND_CATEGORY,
    KIND_OPTION,
    KIND_PRODUCT,
    METHOD_EXACT,
    METHOD_MANUAL,
    ExternalItemMap,
)
from app.models.modifier import ModifierOption
from app.models.product import Product
from app.services.aggregators.menu_normalized import NormalizedMenu
from app.services.catalog.external_item_map_service import normalize_ref


@dataclass
class MappingReport:
    system: str
    products_matched: int = 0
    products_unmatched: list[str] = field(default_factory=list)
    options_matched: int = 0
    options_unmatched: list[str] = field(default_factory=list)
    categories_matched: int = 0
    approved: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "products_matched": self.products_matched,
            "products_unmatched": self.products_unmatched,
            "options_matched": self.options_matched,
            "options_unmatched": self.options_unmatched,
            "categories_matched": self.categories_matched,
            "approved": self.approved,
        }


async def _product_index(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(Product.id, Product.name))).all()
    return {normalize_ref(n): pid for pid, n in rows if n}


async def _category_index(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(Category.id, Category.name))).all()
    return {normalize_ref(n): cid for cid, n in rows if n}


async def _option_index(db: AsyncSession) -> dict[tuple[str, Any], Any]:
    """MM modifier options keyed by (normalised name, price) — the pair an
    aggregator's option read carries, since a bare name is ambiguous."""
    rows = (
        await db.execute(
            select(ModifierOption.id, ModifierOption.name, ModifierOption.price)
        )
    ).all()
    out: dict[tuple[str, Any], Any] = {}
    for oid, name, price in rows:
        if name is not None and price is not None:
            out[(normalize_ref(name), Decimal(str(price)))] = oid
    return out


async def _upsert(
    db: AsyncSession,
    *,
    system: str,
    external_ref: str,
    external_name: str | None,
    mm_kind: str,
    product_id: Any = None,
    modifier_option_id: Any = None,
    category_id: Any = None,
    approve: bool,
) -> bool:
    """Insert or update one map row to point at the matched entity. Returns True if
    it was approved. Never un-approves or overwrites a human's manual edit."""
    ref = normalize_ref(external_ref)
    if ref is None:
        return False
    row = (
        await db.execute(
            select(ExternalItemMap).where(
                ExternalItemMap.system == system,
                ExternalItemMap.external_ref == ref,
                ExternalItemMap.mm_kind == mm_kind,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.match_method == METHOD_MANUAL:
        # A human — or a prior approve pass, which also stamps `manual` — owns this
        # row. Never overwrite or re-point it; an already-approved exact match is
        # simply left as it is (idempotent re-runs).
        return bool(row.approved)
    if row is None:
        row = ExternalItemMap(system=system, external_ref=ref, mm_kind=mm_kind)
        db.add(row)
    row.external_name = (external_name or "").strip()[:255] or row.external_name
    row.product_id = product_id
    row.modifier_option_id = modifier_option_id
    row.category_id = category_id
    if approve:
        row.approved = True
        row.match_method = METHOD_MANUAL
        row.approved_by = "catalog-mapping"
    else:
        row.match_method = row.match_method or METHOD_EXACT
    await db.flush()
    return bool(row.approved)


async def resolve_menu(
    db: AsyncSession,
    system: str,
    menu: NormalizedMenu,
    *,
    approve_exact: bool = True,
) -> MappingReport:
    """Record `external_item_map` rows for every category/item/option in a fetched
    menu, matched to MM. Exact matches are approved when `approve_exact`."""
    rep = MappingReport(system=system)
    products = await _product_index(db)
    categories = await _category_index(db)
    options = await _option_index(db)

    for cat in menu.categories:
        cid = categories.get(normalize_ref(cat.name))
        if cid is not None:
            rep.categories_matched += 1
            await _upsert(
                db,
                system=system,
                external_ref=cat.name,
                external_name=cat.name,
                mm_kind=KIND_CATEGORY,
                category_id=cid,
                approve=approve_exact,
            )
        for item in cat.items:
            pid = products.get(normalize_ref(item.name))
            if pid is not None:
                rep.products_matched += 1
                if await _upsert(
                    db,
                    system=system,
                    external_ref=item.name,
                    external_name=item.name,
                    mm_kind=KIND_PRODUCT,
                    product_id=pid,
                    approve=approve_exact,
                ):
                    rep.approved += 1
            else:
                rep.products_unmatched.append(item.name)
                await _upsert(
                    db,
                    system=system,
                    external_ref=item.name,
                    external_name=item.name,
                    mm_kind=KIND_PRODUCT,
                    approve=False,
                )
            for group in item.modifier_groups:
                for opt in group.options:
                    if opt.price is None:
                        continue
                    oid = options.get(
                        (normalize_ref(opt.name), Decimal(str(opt.price)))
                    )
                    ref = opt.external_ref or opt.name
                    if oid is not None:
                        rep.options_matched += 1
                        if await _upsert(
                            db,
                            system=system,
                            external_ref=ref,
                            external_name=opt.name,
                            mm_kind=KIND_OPTION,
                            modifier_option_id=oid,
                            approve=approve_exact,
                        ):
                            rep.approved += 1
                    else:
                        rep.options_unmatched.append(f"{opt.name}@{opt.price}")
    return rep
