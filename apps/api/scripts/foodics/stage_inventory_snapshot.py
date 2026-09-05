"""Reconcile and optionally stage a reviewed Foodics inventory snapshot.

Dry-run is the default. ``--stage`` may correct item units/cost and create
unapproved Foodics mappings plus draft recipe versions; it never imports
balances, activates recipes, or writes back to Foodics.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pathlib
import sys
import uuid
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from app.core.config import settings  # noqa: E402
from app.models.external_item_map import (  # noqa: E402
    KIND_INVENTORY_ITEM,
    KIND_OPTION,
    KIND_PRODUCT,
    METHOD_EXACT,
    ExternalItemMap,
)
from app.models.inventory import InventoryItem  # noqa: E402
from app.models.modifier import ModifierOption  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.services.inventory import recipe_service  # noqa: E402


def _sha(data: dict[str, Any]) -> str:
    body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _id(row: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value:
            return str(value)
    return None


def _quantity(row: dict[str, Any]) -> Decimal:
    return Decimal(str(row.get("quantity") or row.get("amount") or 0))


def _unit_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return str(value).strip() if value else None


async def _one_by_sku(db, model, sku: str | None):
    if not sku:
        return None, False
    rows = list(
        (await db.execute(select(model).where(model.sku == sku))).scalars().all()
    )
    return (rows[0] if len(rows) == 1 else None), len(rows) > 1


async def _existing_map(db, external_ref: str):
    return (
        (
            await db.execute(
                select(ExternalItemMap).where(
                    ExternalItemMap.system == "foodics",
                    ExternalItemMap.external_ref == external_ref,
                )
            )
        )
        .scalars()
        .first()
    )


async def reconcile(db, snapshot: dict[str, Any], *, stage: bool) -> dict[str, Any]:
    data = snapshot.get("data") or {}
    actual_hash = _sha(data)
    if actual_hash != snapshot.get("content_sha256"):
        raise ValueError("Snapshot content hash does not match its manifest")
    if not snapshot.get("complete"):
        raise ValueError("A partial Foodics snapshot cannot be staged")

    outcome: list[dict[str, Any]] = []
    maps: dict[tuple[str, str], uuid.UUID] = {}
    units = {str(row.get("id")): row for row in data.get("units", [])}
    source_skus = Counter(
        str(row.get("sku") or "").strip().casefold()
        for row in data.get("inventory_items", [])
        if str(row.get("sku") or "").strip()
    )

    for source in data.get("inventory_items", []):
        external_id = str(source["id"])
        sku = str(source.get("sku") or "").strip()
        mapped = await _existing_map(db, external_id)
        item = (
            await db.get(InventoryItem, mapped.inventory_item_id)
            if mapped and mapped.inventory_item_id
            else None
        )
        ambiguous = False
        if item is None:
            item, ambiguous = await _one_by_sku(db, InventoryItem, sku)
        ambiguous = ambiguous or bool(sku and source_skus[sku.casefold()] > 1)

        storage_unit_row = (
            units.get(str(source.get("storage_unit_id")))
            or source.get("storage_unit")
            or {}
        )
        ingredient_unit_row = (
            units.get(str(source.get("ingredient_unit_id")))
            or source.get("ingredient_unit")
            or {}
        )
        storage_unit = (
            _unit_name(storage_unit_row)
            or source.get("storage_unit_name")
            or (item.storage_unit if item else None)
            or "unit"
        )
        ingredient_unit = (
            _unit_name(ingredient_unit_row)
            or source.get("ingredient_unit_name")
            or (item.ingredient_unit if item else None)
            or "unit"
        )
        raw_factor = source.get("storage_to_ingredient_factor")
        if raw_factor is None:
            raw_factor = source.get("factor")
        factor = Decimal(
            str(
                raw_factor
                if raw_factor is not None
                else item.storage_to_ingredient_factor
                if item
                else 1
            )
        )
        raw_cost = source.get("cost")
        cost = Decimal(
            str(raw_cost if raw_cost is not None else item.cost if item else 0)
        )
        name = str(source.get("name") or sku)
        errors = []
        if not sku:
            errors.append("missing SKU")
        if factor <= 0:
            errors.append("invalid conversion factor")
        if mapped is not None and item is None:
            errors.append("existing mapping target is missing")
        action = (
            "ambiguous"
            if ambiguous
            else "orphan"
            if mapped is not None and item is None
            else "conflict"
            if errors
            else "create"
            if item is None
            else "update"
            if (
                item.name != name
                or item.storage_unit != storage_unit
                or item.ingredient_unit != ingredient_unit
                or Decimal(str(item.storage_to_ingredient_factor)) != factor
                or Decimal(str(item.cost)) != cost
            )
            else "unchanged"
        )
        outcome.append(
            {
                "entity": "inventory_item",
                "external_id": external_id,
                "sku": sku,
                "name": name,
                "action": action,
                "errors": errors,
            }
        )
        if ambiguous or errors:
            continue

        if stage:
            if item is None:
                item = InventoryItem(
                    sku=sku,
                    name=name,
                    storage_unit=storage_unit,
                    ingredient_unit=ingredient_unit,
                    storage_to_ingredient_factor=factor,
                    cost=cost,
                )
                db.add(item)
                await db.flush()
            item.name = name
            item.storage_unit = storage_unit
            item.ingredient_unit = ingredient_unit
            item.storage_to_ingredient_factor = factor
            item.cost = cost
            if mapped is None:
                mapped = ExternalItemMap(
                    system="foodics",
                    external_ref=external_id,
                    external_name=source.get("name"),
                    mm_kind=KIND_INVENTORY_ITEM,
                    inventory_item_id=item.id,
                    match_method=METHOD_EXACT,
                    match_score=100,
                    approved=False,
                    notes=f"Staged from Foodics snapshot {actual_hash}",
                )
                db.add(mapped)
        maps[(KIND_INVENTORY_ITEM, external_id)] = (
            item.id
            if item
            else uuid.uuid5(uuid.NAMESPACE_URL, f"foodics:{external_id}")
        )

    async def map_catalogue(kind: str, model, rows: list[dict[str, Any]]) -> None:
        for source in rows:
            external_id = str(source["id"])
            mapped = await _existing_map(db, external_id)
            field = "product_id" if kind == KIND_PRODUCT else "modifier_option_id"
            entity = (
                await db.get(model, getattr(mapped, field))
                if mapped and getattr(mapped, field)
                else None
            )
            ambiguous = False
            if entity is None and hasattr(model, "sku"):
                entity, ambiguous = await _one_by_sku(db, model, source.get("sku"))
            action = "ambiguous" if ambiguous else "unchanged" if entity else "orphan"
            outcome.append(
                {
                    "entity": kind,
                    "external_id": external_id,
                    "name": source.get("name"),
                    "action": action,
                }
            )
            if entity:
                maps[(kind, external_id)] = entity.id

    await map_catalogue(KIND_PRODUCT, Product, data.get("products", []))
    await map_catalogue(KIND_OPTION, ModifierOption, data.get("modifier_options", []))

    recipe_sets = (
        (
            "inventory_item",
            KIND_INVENTORY_ITEM,
            data.get("inventory_item_ingredients", []),
            ("inventory_item_id", "parent_id"),
        ),
        ("product", KIND_PRODUCT, data.get("product_ingredients", []), ("product_id",)),
        (
            "modifier_option",
            KIND_OPTION,
            data.get("modifier_option_ingredients", []),
            ("modifier_option_id", "option_id"),
        ),
    )
    staged_recipes = 0
    for owner_kind, map_kind, rows, owner_fields in recipe_sets:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            owner_ref = _id(row, *owner_fields)
            if owner_ref:
                grouped[owner_ref].append(row)
        for owner_ref, ingredients in grouped.items():
            owner_id = maps.get((map_kind, owner_ref))
            lines: list[recipe_service.RecipeLineInput] = []
            missing = []
            invalid = []
            seen_items: set[uuid.UUID] = set()
            for ingredient in ingredients:
                ingredient_ref = _id(
                    ingredient, "ingredient_id", "child_item_id", "item_id"
                )
                item_id = maps.get((KIND_INVENTORY_ITEM, ingredient_ref or ""))
                if item_id is None:
                    missing.append(ingredient_ref)
                    continue
                recipe_quantity = _quantity(ingredient)
                recipe_yield = Decimal(str(ingredient.get("yield_percentage") or 1))
                if recipe_quantity <= 0 or recipe_yield <= 0 or recipe_yield > 1:
                    invalid.append(ingredient.get("id"))
                    continue
                if item_id in seen_items:
                    invalid.append(ingredient.get("id"))
                    continue
                seen_items.add(item_id)
                lines.append(
                    recipe_service.RecipeLineInput(
                        item_id=item_id,
                        quantity=recipe_quantity,
                        yield_percentage=recipe_yield,
                        inactive_in_order_types=ingredient.get(
                            "inactive_in_order_types"
                        )
                        or [],
                        source_metadata={"foodics_line_id": ingredient.get("id")},
                    )
                )
            existing_recipe = (
                await recipe_service.get_recipe(db, owner_kind, owner_id)
                if owner_id
                else None
            )
            same_snapshot = bool(
                existing_recipe
                and any(
                    version.source == "foodics"
                    and version.source_payload_hash == actual_hash
                    for version in existing_recipe.versions
                )
            )
            conflicting_draft = bool(
                existing_recipe
                and any(
                    version.status == "draft"
                    and (
                        version.source != "foodics"
                        or version.source_payload_hash not in {None, actual_hash}
                    )
                    for version in existing_recipe.versions
                )
            )
            action = (
                "conflict"
                if not owner_id or missing or invalid or not lines or conflicting_draft
                else "unchanged"
                if same_snapshot
                else "create"
            )
            outcome.append(
                {
                    "entity": f"{owner_kind}_recipe",
                    "external_id": owner_ref,
                    "action": action,
                    "missing_inventory_item_ids": missing,
                    "invalid_line_ids": invalid,
                }
            )
            if stage and action == "create" and owner_id:
                await recipe_service.create_draft(
                    db,
                    kind=owner_kind,
                    owner_id=owner_id,
                    lines=lines,
                    source="foodics",
                    source_payload_hash=actual_hash,
                    source_metadata={"foodics_owner_id": owner_ref},
                )
                staged_recipes += 1

    return {
        "snapshot_sha256": actual_hash,
        "mode": "stage" if stage else "dry_run",
        "summary": dict(Counter(row["action"] for row in outcome)),
        "staged_recipes": staged_recipes,
        "rows": outcome,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=pathlib.Path)
    parser.add_argument("--report", type=pathlib.Path)
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            report = await reconcile(db, snapshot, stage=args.stage)
            if args.stage:
                await db.commit()
            else:
                await db.rollback()
    finally:
        await engine.dispose()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.report:
        args.report.write_text(rendered)
    print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
