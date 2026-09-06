"""Stage one-to-one resale recipes where product and stock SKU are identical.

This is intentionally narrower than the Foodics recipe importer.  It is for
already audited direct-resale products (for example, 7UP and Gift Note Card),
where the MM product and the stocked inventory item have the same immutable SKU
and normalised name.  It never guesses recipes for made products, modifiers,
or items with only similar names.

Dry-run is the default.  ``--stage`` only creates *draft* recipe versions; it
cannot activate a recipe, post a movement, alter stock, or change availability.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.inventory import InventoryItem
from app.models.inventory_v2 import Recipe
from app.models.product import Product
from app.services.inventory import recipe_service

SOURCE = "catalogue-sku-seed"


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _snapshot_hash(product: Product, item: InventoryItem) -> str:
    payload = {
        "product_id": str(product.id),
        "product_sku": product.sku,
        "inventory_item_id": str(item.id),
        "inventory_item_sku": item.sku,
        "quantity": "1",
        "ingredient_unit": item.ingredient_unit,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def reconcile(db, *, stage: bool) -> dict[str, object]:
    """Return an auditable plan and optionally stage conflict-free drafts."""
    rows = (
        await db.execute(
            select(Product, InventoryItem)
            .join(InventoryItem, InventoryItem.sku == Product.sku)
            .where(
                Product.sku.is_not(None),
                Product.sku != "",
                InventoryItem.kind == "resale_good",
                InventoryItem.tracking_mode == "stocked",
                InventoryItem.is_active.is_(True),
            )
            .order_by(Product.sku)
        )
    ).all()

    report_rows: list[dict[str, str]] = []
    staged = 0
    for product, item in rows:
        row = {
            "product_id": str(product.id),
            "product_sku": product.sku,
            "inventory_item_id": str(item.id),
            "inventory_item_sku": item.sku,
        }
        if _normalise_name(product.name) != _normalise_name(item.name):
            report_rows.append({**row, "action": "conflict", "reason": "name_mismatch"})
            continue

        recipe = (
            (
                await db.execute(
                    select(Recipe)
                    .where(Recipe.product_id == product.id)
                    .options(selectinload(Recipe.versions))
                )
            )
            .scalars()
            .unique()
            .one_or_none()
        )
        payload_hash = _snapshot_hash(product, item)
        if recipe is not None:
            if any(
                version.source == SOURCE and version.source_payload_hash == payload_hash
                for version in recipe.versions
            ):
                report_rows.append(
                    {**row, "action": "unchanged", "reason": "same_snapshot"}
                )
            else:
                report_rows.append(
                    {**row, "action": "conflict", "reason": "existing_recipe"}
                )
            continue

        report_rows.append({**row, "action": "create", "reason": "exact_sku_and_name"})
        if stage:
            await recipe_service.create_draft(
                db,
                kind="product",
                owner_id=product.id,
                lines=[
                    recipe_service.RecipeLineInput(
                        item_id=item.id,
                        quantity=Decimal("1"),
                        yield_percentage=Decimal("1"),
                        source_metadata={
                            "seed_rule": "exact_product_and_inventory_sku",
                            "product_sku": product.sku,
                            "inventory_item_sku": item.sku,
                        },
                    )
                ],
                source=SOURCE,
                source_payload_hash=payload_hash,
                source_metadata={
                    "seed_rule": "exact_product_and_inventory_sku",
                    "product_sku": product.sku,
                    "inventory_item_sku": item.sku,
                },
            )
            staged += 1

    return {
        "mode": "stage" if stage else "dry_run",
        "summary": dict(Counter(row["action"] for row in report_rows)),
        "staged_recipes": staged,
        "rows": report_rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", action="store_true")
    args = parser.parse_args()

    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            report = await reconcile(db, stage=args.stage)
            if args.stage:
                await db.commit()
            else:
                await db.rollback()
    finally:
        await engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
