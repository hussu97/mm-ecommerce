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

    for source in data.get("inventory_items", []):
        external_id = str(source["id"])
        mapped = await _existing_map(db, external_id)
        item = (
            await db.get(InventoryItem, mapped.inventory_item_id)
            if mapped and mapped.inventory_item_id
            else None
        )
        ambiguous = False
        if item is None:
            item, ambiguous = await _one_by_sku(db, InventoryItem, source.get("sku"))
        action = "ambiguous" if ambiguous else "update" if item else "create"
        if mapped and item:
            action = "unchanged"
        row_result = {
            "entity": "inventory_item",
            "external_id": external_id,
            "sku": source.get("sku"),
            "name": source.get("name"),
            "action": action,
        }
        outcome.append(row_result)
        if ambiguous or item is None:
            continue

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
            storage_unit_row.get("name")
            or source.get("storage_unit_name")
            or item.storage_unit
        )
        ingredient_unit = (
            ingredient_unit_row.get("name")
            or source.get("ingredient_unit_name")
            or item.ingredient_unit
        )
        factor = Decimal(
            str(
                source.get("storage_to_ingredient_factor")
                or source.get("factor")
                or item.storage_to_ingredient_factor
            )
        )
        if stage:
            item.storage_unit = storage_unit
            item.ingredient_unit = ingredient_unit
            item.storage_to_ingredient_factor = factor
            if source.get("cost") is not None:
                item.cost = Decimal(str(source["cost"]))
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
        maps[(KIND_INVENTORY_ITEM, external_id)] = item.id

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
            for ingredient in ingredients:
                ingredient_ref = _id(
                    ingredient, "ingredient_id", "child_item_id", "item_id"
                )
                item_id = maps.get((KIND_INVENTORY_ITEM, ingredient_ref or ""))
                if item_id is None:
                    missing.append(ingredient_ref)
                    continue
                lines.append(
                    recipe_service.RecipeLineInput(
                        item_id=item_id,
                        quantity=_quantity(ingredient),
                        yield_percentage=Decimal(
                            str(ingredient.get("yield_percentage") or 1)
                        ),
                        inactive_in_order_types=ingredient.get(
                            "inactive_in_order_types"
                        )
                        or [],
                        source_metadata={"foodics_line_id": ingredient.get("id")},
                    )
                )
            action = "conflict" if not owner_id or missing or not lines else "create"
            outcome.append(
                {
                    "entity": f"{owner_kind}_recipe",
                    "external_id": owner_ref,
                    "action": action,
                    "missing_inventory_item_ids": missing,
                }
            )
            if stage and owner_id and lines and not missing:
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
