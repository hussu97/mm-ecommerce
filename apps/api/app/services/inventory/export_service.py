from __future__ import annotations

import csv
import io
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.category import Category
from app.models.inventory import InventoryItem
from app.models.inventory_v2 import Recipe, RecipeVersion, RecipeVersionStatusEnum
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.order import Order, OrderStatusEnum
from app.models.product import Product

__all__ = [
    "export_categories",
    "export_inventory_items",
    "export_modifier_options",
    "export_modifiers",
    "export_orders",
    "export_product_modifiers",
    "export_products",
    "export_recipes",
]


async def export_categories(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(select(Category).order_by(Category.display_order))
    rows = result.scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["id", "name"]
    for code in languages:
        header.extend([f"name_{code}", f"description_{code}"])
    header.extend(["reference", "image", "display_order", "is_active"])
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [str(r.id), r.name]
        for code in languages:
            lang_t = t.get(code, {})
            row_data.extend([lang_t.get("name", ""), lang_t.get("description", "")])
        row_data.extend(
            [r.reference or "", r.image_url or "", r.display_order, str(r.is_active)]
        )
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
    header.extend(
        [
            "is_active",
            "is_stock_product",
            "stock_quantity",
            "calories",
            "preparation_time",
            "cost",
            "barcode",
            "display_order",
            "is_featured",
            "is_sold_by_weight",
            "sync_to_aggregators",
        ]
    )
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
                str(r.stock_quantity),
                str(r.calories) if r.calories else "",
                str(r.preparation_time) if r.preparation_time else "",
                str(r.cost) if r.cost is not None else "",
                r.barcode or "",
                str(r.display_order),
                str(r.is_featured),
                str(r.is_sold_by_weight),
                str(r.sync_to_aggregators),
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
    header.append("is_active")
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [str(r.id), r.reference, r.name]
        for code in languages:
            row_data.append(t.get(code, {}).get("name", ""))
        row_data.append(str(r.is_active))
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
    header = ["id", "modifier_reference", "name", "sku", "price", "cost", "calories"]
    for code in languages:
        header.append(f"name_{code}")
    header.extend(["is_active", "display_order"])
    w.writerow(header)
    for r in rows:
        t = r.translations or {}
        row_data: list[str] = [
            str(r.id),
            r.modifier.reference,
            r.name,
            r.sku,
            str(r.price),
            str(r.cost) if r.cost is not None else "",
            str(r.calories) if r.calories is not None else "",
        ]
        for code in languages:
            row_data.append(t.get(code, {}).get("name", ""))
        row_data.extend([str(r.is_active), str(r.display_order)])
        w.writerow(row_data)
    return buf.getvalue()


async def export_orders(
    db: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    status: Optional[OrderStatusEnum] = None,
) -> str:
    stmt = (
        select(Order)
        .options(joinedload(Order.items), joinedload(Order.delivery))
        .order_by(Order.created_at.desc())
    )
    if start_date:
        stmt = stmt.where(Order.created_at >= start_date)
    if end_date:
        from datetime import timedelta

        stmt = stmt.where(Order.created_at < (end_date + timedelta(days=1)))
    if status:
        stmt = stmt.where(Order.status == status)

    result = await db.execute(stmt)
    rows = result.scalars().unique().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "Order #",
            "Date",
            "Customer Email",
            "Status",
            "Items Count",
            "Subtotal",
            "Discount",
            "Delivery Fee",
            "Small Order Fee",
            "Total",
            "Payment Provider",
            "Delivery Method",
            "Delivery Zone",
            "Promo Code",
        ]
    )
    for r in rows:
        # The zone that priced the order, not the emirate the customer picked
        # from a dropdown that no longer exists.
        zone = r.delivery.zone_name if r.delivery else ""
        w.writerow(
            [
                r.order_number,
                r.created_at.strftime("%Y-%m-%d"),
                r.email,
                r.status.value,
                len(r.items),
                str(r.subtotal),
                str(r.discount_amount),
                str(r.delivery_fee),
                # Next to the delivery fee rather than appended at the end,
                # because the two read together — and the header above moves
                # with it, which is the only thing that keeps this file's
                # positional columns honest.
                str(r.low_order_fee or 0),
                str(r.total),
                r.payment_provider or "",
                r.delivery_method.value,
                zone or "",
                r.promo_code_used or "",
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
            "display_order",
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
                r.display_order,
            ]
        )
    return buf.getvalue()


async def export_inventory_items(db: AsyncSession) -> str:
    """The operator-editable inventory catalogue template.

    IDs and SKUs are both included so imports can detect a stale or accidentally
    copied identifier rather than guessing from a human-readable name.
    """
    rows = (
        (
            await db.execute(
                select(InventoryItem)
                .options(joinedload(InventoryItem.category))
                .order_by(InventoryItem.count_order, InventoryItem.name)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "sku",
            "name",
            "barcode",
            "category_reference",
            "kind",
            "tracking_mode",
            "storage_unit",
            "ingredient_unit",
            "storage_to_ingredient_factor",
            "cost",
            "costing_method",
            "yield_percentage",
            "minimum_level",
            "par_level",
            "maximum_level",
            "is_product",
            "storage_zone",
            "count_order",
            "is_active",
        ]
    )
    for item in rows:
        writer.writerow(
            [
                str(item.id),
                item.sku,
                item.name,
                item.barcode or "",
                item.category.reference if item.category else "",
                item.kind,
                item.tracking_mode,
                item.storage_unit,
                item.ingredient_unit,
                str(item.storage_to_ingredient_factor),
                str(item.cost),
                item.costing_method,
                str(item.yield_percentage),
                str(item.minimum_level),
                str(item.par_level),
                str(item.maximum_level),
                str(item.is_product),
                item.storage_zone or "",
                item.count_order,
                str(item.is_active),
            ]
        )
    return buf.getvalue()


async def export_recipes(db: AsyncSession) -> str:
    """Export one editable recipe version per owner.

    A draft takes precedence over the active version so a downloaded file is an
    honest representation of what an operator would edit next. Importing this
    file only stages drafts; activation is intentionally kept in the owner UI.
    """
    recipes = (
        (
            await db.execute(
                select(Recipe).options(
                    joinedload(Recipe.versions).joinedload(RecipeVersion.lines)
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    product_ids = {recipe.product_id for recipe in recipes if recipe.product_id}
    option_ids = {
        recipe.modifier_option_id for recipe in recipes if recipe.modifier_option_id
    }
    item_ids = {
        recipe.inventory_item_id for recipe in recipes if recipe.inventory_item_id
    }
    ingredient_ids = {
        line.item_id
        for recipe in recipes
        for version in recipe.versions
        for line in version.lines
    }
    products = (
        {
            value.id: value
            for value in (
                await db.execute(select(Product).where(Product.id.in_(product_ids)))
            ).scalars()
        }
        if product_ids
        else {}
    )
    options = (
        {
            value.id: value
            for value in (
                await db.execute(
                    select(ModifierOption).where(ModifierOption.id.in_(option_ids))
                )
            ).scalars()
        }
        if option_ids
        else {}
    )
    items = (
        {
            value.id: value
            for value in (
                await db.execute(
                    select(InventoryItem).where(
                        InventoryItem.id.in_(item_ids | ingredient_ids)
                    )
                )
            ).scalars()
        }
        if (item_ids or ingredient_ids)
        else {}
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "owner_kind",
            "owner_id",
            "owner_sku",
            "owner_name",
            "exported_version",
            "exported_status",
            "ingredient_item_id",
            "ingredient_sku",
            "ingredient_name",
            "quantity",
            "ingredient_unit",
            "yield_percentage",
            "inactive_in_order_types",
            "display_order",
        ]
    )
    for recipe in sorted(recipes, key=lambda value: (value.owner_kind, str(value.id))):
        draft = next(
            (
                v
                for v in recipe.versions
                if v.status == RecipeVersionStatusEnum.DRAFT.value
            ),
            None,
        )
        active = next(
            (
                v
                for v in recipe.versions
                if v.status == RecipeVersionStatusEnum.ACTIVE.value
            ),
            None,
        )
        version = draft or active
        if version is None:
            continue
        owner_id = (
            recipe.product_id or recipe.modifier_option_id or recipe.inventory_item_id
        )
        if owner_id is None:
            continue
        owner = products.get(owner_id) or options.get(owner_id) or items.get(owner_id)
        owner_sku = getattr(owner, "sku", "") or ""
        owner_name = getattr(owner, "name", "") or ""
        for line in sorted(version.lines, key=lambda value: value.display_order):
            ingredient = items.get(line.item_id)
            writer.writerow(
                [
                    recipe.owner_kind,
                    str(owner_id),
                    owner_sku,
                    owner_name,
                    version.version_number,
                    version.status,
                    str(line.item_id),
                    ingredient.sku if ingredient else "",
                    ingredient.name if ingredient else "",
                    str(line.quantity),
                    line.ingredient_unit,
                    str(line.yield_percentage),
                    "|".join(line.inactive_in_order_types or []),
                    line.display_order,
                ]
            )
    return buf.getvalue()
