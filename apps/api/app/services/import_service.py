"""
CSV import service for Foodics catalog data.
Each function accepts a list of dicts (from csv.DictReader) and upserts records.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.schemas.import_data import ImportError, ImportResult


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "item"


def _parse_decimal(val: str, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(val).strip() or "0")
    except InvalidOperation:
        return default


def _parse_bool(val: str) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes")


def _parse_int(val: str, default: int = 0) -> int:
    try:
        return int(str(val).strip() or default)
    except (ValueError, TypeError):
        return default


async def import_categories(db: AsyncSession, rows: list[dict]) -> ImportResult:
    result = ImportResult()

    for i, row in enumerate(rows, start=1):
        try:
            reference = (row.get("reference") or "").strip()
            name = (row.get("name") or "").strip()
            if not name:
                result.errors.append(ImportError(row=i, message="Missing name"))
                result.skipped += 1
                continue

            foodics_id = (row.get("id") or "").strip()
            name_ar = (
                row.get("name_localized") or row.get("name_ar") or ""
            ).strip() or None
            image_url = (row.get("image") or "").strip() or None
            translations: dict = {}
            if name_ar:
                translations["ar"] = {"name": name_ar}

            # Build slug from reference if available, else name
            slug_base = reference if reference else name
            slug = _slugify(slug_base)

            # Try to find existing category by reference first, then by id
            existing = None
            if reference:
                res = await db.execute(
                    select(Category).where(Category.reference == reference)
                )
                existing = res.scalar_one_or_none()

            if existing is None and foodics_id:
                try:
                    fid = uuid.UUID(foodics_id)
                    res = await db.execute(select(Category).where(Category.id == fid))
                    existing = res.scalar_one_or_none()
                except ValueError:
                    pass

            if existing:
                existing.name = name
                if translations:
                    existing.translations = translations
                existing.reference = reference or existing.reference
                if image_url:
                    existing.image_url = image_url
                result.updated += 1
            else:
                # Ensure slug is unique
                base_slug = slug
                counter = 1
                while True:
                    slug_check = await db.execute(
                        select(Category).where(Category.slug == slug)
                    )
                    if not slug_check.scalar_one_or_none():
                        break
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                cat_id = None
                if foodics_id:
                    try:
                        cat_id = uuid.UUID(foodics_id)
                    except ValueError:
                        pass

                cat = Category(
                    id=cat_id or uuid.uuid4(),
                    name=name,
                    translations=translations,
                    slug=slug,
                    reference=reference or None,
                    image_url=image_url,
                )
                db.add(cat)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result


async def import_products(db: AsyncSession, rows: list[dict]) -> ImportResult:
    result = ImportResult()

    for i, row in enumerate(rows, start=1):
        try:
            sku = (row.get("sku") or "").strip()
            name = (row.get("name") or "").strip()
            if not name:
                result.errors.append(ImportError(row=i, message="Missing name"))
                result.skipped += 1
                continue

            foodics_id = (row.get("id") or "").strip()
            name_ar = (
                row.get("name_localized") or row.get("name_ar") or ""
            ).strip() or None
            description = (row.get("description") or "").strip() or None
            desc_ar = (
                row.get("description_localized") or row.get("description_ar") or ""
            ).strip() or None
            prod_translations: dict = {}
            if name_ar or desc_ar:
                ar_fields: dict[str, str] = {}
                if name_ar:
                    ar_fields["name"] = name_ar
                if desc_ar:
                    ar_fields["description"] = desc_ar
                prod_translations["ar"] = ar_fields
            category_ref = (row.get("category_reference") or "").strip()
            base_price = _parse_decimal(row.get("price", "0"))
            image_url = (row.get("image") or "").strip() or None
            is_active = _parse_bool(row.get("is_active", "1"))
            is_stock_product = _parse_bool(row.get("is_stock_product", "0"))
            calories = (
                _parse_int(row.get("calories", ""))
                if row.get("calories", "").strip()
                else None
            )
            prep_time = (
                _parse_int(row.get("preparation_time", ""))
                if row.get("preparation_time", "").strip()
                else None
            )

            # Resolve category
            category_id = None
            if category_ref:
                cat_res = await db.execute(
                    select(Category).where(Category.reference == category_ref)
                )
                cat = cat_res.scalar_one_or_none()
                if cat:
                    category_id = cat.id

            # Find existing product by SKU
            existing = None
            if sku:
                res = await db.execute(select(Product).where(Product.sku == sku))
                existing = res.scalar_one_or_none()

            if existing is None and foodics_id:
                try:
                    fid = uuid.UUID(foodics_id)
                    res = await db.execute(select(Product).where(Product.id == fid))
                    existing = res.scalar_one_or_none()
                except ValueError:
                    pass

            if existing:
                existing.name = name
                existing.sku = sku or existing.sku
                existing.description = description
                if prod_translations:
                    existing.translations = prod_translations
                existing.base_price = base_price
                existing.category_id = category_id
                existing.is_active = is_active
                existing.is_stock_product = is_stock_product
                if calories is not None:
                    existing.calories = calories
                if prep_time is not None:
                    existing.preparation_time = prep_time
                if image_url:
                    existing.image_urls = [image_url]
                result.updated += 1
            else:
                slug = _slugify(name)
                base_slug = slug
                counter = 1
                while True:
                    slug_check = await db.execute(
                        select(Product).where(Product.slug == slug)
                    )
                    if not slug_check.scalar_one_or_none():
                        break
                    slug = f"{base_slug}-{counter}"
                    counter += 1

                prod_id = None
                if foodics_id:
                    try:
                        prod_id = uuid.UUID(foodics_id)
                    except ValueError:
                        pass

                product = Product(
                    id=prod_id or uuid.uuid4(),
                    name=name,
                    translations=prod_translations,
                    slug=slug,
                    sku=sku or None,
                    description=description,
                    base_price=base_price,
                    category_id=category_id,
                    is_active=is_active,
                    is_stock_product=is_stock_product,
                    calories=calories,
                    preparation_time=prep_time,
                    image_urls=[image_url] if image_url else [],
                )
                db.add(product)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result


async def import_modifiers(db: AsyncSession, rows: list[dict]) -> ImportResult:
    result = ImportResult()

    for i, row in enumerate(rows, start=1):
        try:
            reference = (row.get("reference") or "").strip()
            name = (row.get("name") or "").strip()
            if not reference or not name:
                result.errors.append(
                    ImportError(row=i, message="Missing reference or name")
                )
                result.skipped += 1
                continue

            foodics_id = (row.get("id") or "").strip()
            mod_name_ar = (
                row.get("name_localized") or row.get("name_ar") or ""
            ).strip() or None
            mod_translations: dict = {}
            if mod_name_ar:
                mod_translations["ar"] = {"name": mod_name_ar}

            res = await db.execute(
                select(Modifier).where(Modifier.reference == reference)
            )
            existing = res.scalar_one_or_none()

            if existing:
                existing.name = name
                if mod_translations:
                    existing.translations = mod_translations
                result.updated += 1
            else:
                mod_id = None
                if foodics_id:
                    try:
                        mod_id = uuid.UUID(foodics_id)
                    except ValueError:
                        pass

                modifier = Modifier(
                    id=mod_id or uuid.uuid4(),
                    reference=reference,
                    name=name,
                    translations=mod_translations,
                )
                db.add(modifier)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result


async def import_modifier_options(db: AsyncSession, rows: list[dict]) -> ImportResult:
    result = ImportResult()

    for i, row in enumerate(rows, start=1):
        try:
            sku = (row.get("sku") or "").strip()
            name = (row.get("name") or "").strip()
            modifier_ref = (row.get("modifier_reference") or "").strip()

            if not sku or not name or not modifier_ref:
                result.errors.append(
                    ImportError(
                        row=i, message="Missing sku, name, or modifier_reference"
                    )
                )
                result.skipped += 1
                continue

            # Resolve modifier
            mod_res = await db.execute(
                select(Modifier).where(Modifier.reference == modifier_ref)
            )
            modifier = mod_res.scalar_one_or_none()
            if not modifier:
                result.errors.append(
                    ImportError(row=i, message=f"Modifier '{modifier_ref}' not found")
                )
                result.skipped += 1
                continue

            foodics_id = (row.get("id") or "").strip()
            opt_name_ar = (
                row.get("name_localized") or row.get("name_ar") or ""
            ).strip() or None
            opt_translations: dict = {}
            if opt_name_ar:
                opt_translations["ar"] = {"name": opt_name_ar}
            price = _parse_decimal(row.get("price", "0"))
            is_active = _parse_bool(row.get("is_active", "1"))

            res = await db.execute(
                select(ModifierOption).where(ModifierOption.sku == sku)
            )
            existing = res.scalar_one_or_none()

            if existing:
                existing.name = name
                if opt_translations:
                    existing.translations = opt_translations
                existing.price = price
                existing.is_active = is_active
                existing.modifier_id = modifier.id
                result.updated += 1
            else:
                opt_id = None
                if foodics_id:
                    try:
                        opt_id = uuid.UUID(foodics_id)
                    except ValueError:
                        pass

                option = ModifierOption(
                    id=opt_id or uuid.uuid4(),
                    modifier_id=modifier.id,
                    name=name,
                    translations=opt_translations,
                    sku=sku,
                    price=price,
                    is_active=is_active,
                )
                db.add(option)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result


async def import_product_modifiers(db: AsyncSession, rows: list[dict]) -> ImportResult:
    result = ImportResult()

    for i, row in enumerate(rows, start=1):
        try:
            product_sku = (row.get("product_sku") or "").strip()
            modifier_ref = (row.get("modifier_reference") or "").strip()

            if not product_sku or not modifier_ref:
                result.errors.append(
                    ImportError(
                        row=i, message="Missing product_sku or modifier_reference"
                    )
                )
                result.skipped += 1
                continue

            # Resolve product
            prod_res = await db.execute(
                select(Product).where(Product.sku == product_sku)
            )
            product = prod_res.scalar_one_or_none()
            if not product:
                result.errors.append(
                    ImportError(row=i, message=f"Product SKU '{product_sku}' not found")
                )
                result.skipped += 1
                continue

            # Resolve modifier
            mod_res = await db.execute(
                select(Modifier).where(Modifier.reference == modifier_ref)
            )
            modifier = mod_res.scalar_one_or_none()
            if not modifier:
                result.errors.append(
                    ImportError(row=i, message=f"Modifier '{modifier_ref}' not found")
                )
                result.skipped += 1
                continue

            min_opts = _parse_int(row.get("minimum_options", "0"))
            max_opts = _parse_int(row.get("maximum_options", "1"))
            free_opts = _parse_int(row.get("free_options", "0"))
            unique_opts = _parse_bool(row.get("unique_options", "0"))

            # Upsert by product_id + modifier_id
            res = await db.execute(
                select(ProductModifier).where(
                    ProductModifier.product_id == product.id,
                    ProductModifier.modifier_id == modifier.id,
                )
            )
            existing = res.scalar_one_or_none()

            if existing:
                existing.minimum_options = min_opts
                existing.maximum_options = max_opts
                existing.free_options = free_opts
                existing.unique_options = unique_opts
                result.updated += 1
            else:
                pm = ProductModifier(
                    product_id=product.id,
                    modifier_id=modifier.id,
                    minimum_options=min_opts,
                    maximum_options=max_opts,
                    free_options=free_opts,
                    unique_options=unique_opts,
                )
                db.add(pm)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result
