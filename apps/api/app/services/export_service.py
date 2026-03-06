from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product


async def export_categories(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(select(Category).order_by(Category.display_order))
    rows = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["id", "name"]
    for code in languages:
        header.extend([f"name_{code}", f"description_{code}"])
    header.extend(["reference", "image"])
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [str(r.id), r.name]
        for code in languages:
            lang_t = t.get(code, {})
            row_data.extend([lang_t.get("name", ""), lang_t.get("description", "")])
        row_data.extend([r.reference or "", r.image_url or ""])
        w.writerow(row_data)
    return buf.getvalue()


async def export_products(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(
        select(Product)
        .options(joinedload(Product.category))
        .order_by(Product.display_order)
    )
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    header = [
        "id",
        "name",
        "sku",
        "category_reference",
        "price",
        "description",
        "image",
    ]
    for code in languages:
        header.extend([f"name_{code}", f"description_{code}"])
    header.extend(["is_active", "is_stock_product", "calories", "preparation_time"])
    w.writerow(header)
    for r in rows:
        category_ref = (
            r.category.reference if r.category and r.category.reference else ""
        )
        image = r.image_urls[0] if r.image_urls else ""
        t = r.translations or {}
        row_data: list[str] = [
            str(r.id),
            r.name,
            r.sku or "",
            category_ref,
            str(r.base_price),
            r.description or "",
            image,
        ]
        for code in languages:
            lang_t = t.get(code, {})
            row_data.extend([lang_t.get("name", ""), lang_t.get("description", "")])
        row_data.extend(
            [
                str(r.is_active),
                str(r.is_stock_product),
                str(r.calories) if r.calories else "",
                str(r.preparation_time) if r.preparation_time else "",
            ]
        )
        w.writerow(row_data)
    return buf.getvalue()


async def export_modifiers(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(select(Modifier).order_by(Modifier.name))
    rows = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["id", "reference", "name"]
    for code in languages:
        header.append(f"name_{code}")
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [str(r.id), r.reference, r.name]
        for code in languages:
            row_data.append(t.get(code, {}).get("name", ""))
        w.writerow(row_data)
    return buf.getvalue()


async def export_modifier_options(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(
        select(ModifierOption)
        .options(joinedload(ModifierOption.modifier))
        .order_by(ModifierOption.display_order)
    )
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["id", "modifier_reference", "name", "sku", "price"]
    for code in languages:
        header.append(f"name_{code}")
    header.append("is_active")
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [
            str(r.id),
            r.modifier.reference,
            r.name,
            r.sku,
            str(r.price),
        ]
        for code in languages:
            row_data.append(t.get(code, {}).get("name", ""))
        row_data.append(str(r.is_active))
        w.writerow(row_data)
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
