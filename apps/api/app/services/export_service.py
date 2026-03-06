from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product


async def export_categories(db: AsyncSession) -> str:
    result = await db.execute(select(Category).order_by(Category.display_order))
    rows = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "name", "name_ar", "reference", "image"])
    for r in rows:
        name_ar = (r.translations or {}).get("ar", {}).get("name", "")
        w.writerow([r.id, r.name, name_ar, r.reference or "", r.image_url or ""])
    return buf.getvalue()


async def export_products(db: AsyncSession) -> str:
    result = await db.execute(
        select(Product)
        .options(joinedload(Product.category))
        .order_by(Product.display_order)
    )
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "name",
            "sku",
            "category_reference",
            "price",
            "description",
            "image",
            "name_ar",
            "description_ar",
            "is_active",
            "is_stock_product",
            "calories",
            "preparation_time",
        ]
    )
    for r in rows:
        category_ref = (
            r.category.reference if r.category and r.category.reference else ""
        )
        image = r.image_urls[0] if r.image_urls else ""
        ar = (r.translations or {}).get("ar", {})
        w.writerow(
            [
                r.id,
                r.name,
                r.sku or "",
                category_ref,
                r.base_price,
                r.description or "",
                image,
                ar.get("name", ""),
                ar.get("description", ""),
                r.is_active,
                r.is_stock_product,
                r.calories or "",
                r.preparation_time or "",
            ]
        )
    return buf.getvalue()


async def export_modifiers(db: AsyncSession) -> str:
    result = await db.execute(select(Modifier).order_by(Modifier.name))
    rows = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "reference", "name", "name_ar"])
    for r in rows:
        name_ar = (r.translations or {}).get("ar", {}).get("name", "")
        w.writerow([r.id, r.reference, r.name, name_ar])
    return buf.getvalue()


async def export_modifier_options(db: AsyncSession) -> str:
    result = await db.execute(
        select(ModifierOption)
        .options(joinedload(ModifierOption.modifier))
        .order_by(ModifierOption.display_order)
    )
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "modifier_reference",
            "name",
            "sku",
            "price",
            "name_ar",
            "is_active",
        ]
    )
    for r in rows:
        name_ar = (r.translations or {}).get("ar", {}).get("name", "")
        w.writerow(
            [
                r.id,
                r.modifier.reference,
                r.name,
                r.sku,
                r.price,
                name_ar,
                r.is_active,
            ]
        )
    return buf.getvalue()


async def export_product_modifiers(db: AsyncSession) -> str:
    result = await db.execute(
        select(ProductModifier)
        .options(
            joinedload(ProductModifier.product),
            joinedload(ProductModifier.modifier),
        )
        .order_by(ProductModifier.display_order)
    )
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "product_sku",
            "modifier_reference",
            "minimum_options",
            "maximum_options",
            "free_options",
            "unique_options",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.product.sku or "",
                r.modifier.reference,
                r.minimum_options,
                r.maximum_options,
                r.free_options,
                r.unique_options,
            ]
        )
    return buf.getvalue()
