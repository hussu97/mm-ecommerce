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
from app.models.inventory import InventoryCategory, InventoryItem
from app.models.inventory_v2 import RecipeOwnerKindEnum
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.schemas.import_data import ImportError, ImportResult
from app.services.inventory import recipe_service

__all__ = [
    "import_categories",
    "import_modifier_options",
    "import_modifiers",
    "import_inventory_items",
    "import_product_modifiers",
    "import_products",
    "import_recipes",
]


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


def _has(row: dict, field: str) -> bool:
    """Whether the CSV carried an actual value for ``field`` on this row.

    A partial-column export (only ``sku,name,stock_quantity``, say) must never
    overwrite the columns it omits — reactivating a soft-deleted row, zeroing a
    price, or uncategorising a product because ``is_active`` / ``price`` /
    ``category_reference`` were simply absent from the header. So an update only
    touches a field the spreadsheet actually spoke to: the column is present in
    the header *and* the cell is non-empty. Clearing a field is a deliberate act
    done in the console, never a side effect of a column that was not exported.
    """
    return field in row and str(row.get(field) or "").strip() != ""


def _required_decimal(row: dict, field: str, row_number: int) -> Decimal:
    raw = str(row.get(field) or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if value <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return value


def _uuid_or_error(value: object, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID from an MM export") from exc


def _extract_translations(
    row: dict,
    fields: list[str],
) -> dict[str, dict[str, str]]:
    """Scan CSV row for `{field}_{langcode}` columns and build translations dict.

    Also handles Foodics compat: `name_localized` -> ar.name,
    `description_localized` -> ar.description.
    """
    translations: dict[str, dict[str, str]] = {}

    for field in fields:
        # Foodics compat: {field}_localized -> ar.{field}
        localized = (row.get(f"{field}_localized") or "").strip()
        if localized:
            translations.setdefault("ar", {})[field] = localized

        # Scan all columns for {field}_{langcode} pattern
        for col_name, col_val in row.items():
            m = re.match(rf"^{re.escape(field)}_([a-z]{{2,10}})$", col_name)
            if m:
                val = (col_val or "").strip()
                if val:
                    lang_code = m.group(1)
                    # Don't overwrite if already set from _localized
                    translations.setdefault(lang_code, {})[field] = val

    return translations


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
            image_url = (row.get("image") or "").strip() or None
            translations = _extract_translations(row, ["name", "description"])
            display_order = _parse_int(row.get("display_order", "0"))
            is_active = _parse_bool(row.get("is_active", "true"))

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
                    merged = dict(existing.translations or {})
                    for lang, fields in translations.items():
                        merged.setdefault(lang, {}).update(fields)
                    existing.translations = merged
                existing.reference = reference or existing.reference
                if "display_order" in row and str(row["display_order"] or "").strip():
                    existing.display_order = display_order
                if "is_active" in row and str(row["is_active"] or "").strip():
                    existing.is_active = is_active
                if image_url:
                    existing.image_url = image_url
                    result.image_urls.append(image_url)
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
                    display_order=display_order,
                    is_active=is_active,
                )
                db.add(cat)
                if image_url:
                    result.image_urls.append(image_url)
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
            description = (row.get("description") or "").strip() or None
            prod_translations = _extract_translations(row, ["name", "description"])
            category_ref = (row.get("category_reference") or "").strip()
            base_price = _parse_decimal(row.get("price", "0"))
            image_url = (row.get("image") or "").strip() or None
            is_active = _parse_bool(row.get("is_active", "1"))
            is_stock_product = _parse_bool(row.get("is_stock_product", "0"))
            stock_quantity_raw = row.get("stock_quantity")
            stock_quantity = (
                max(_parse_int(stock_quantity_raw), 0)
                if stock_quantity_raw is not None and str(stock_quantity_raw).strip()
                else None
            )
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
            cost = (
                _parse_decimal(row.get("cost", "0"))
                if str(row.get("cost") or "").strip()
                else None
            )
            display_order = _parse_int(row.get("display_order", "0"))
            barcode = (row.get("barcode") or "").strip() or None

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
                if _has(row, "description"):
                    existing.description = description
                if prod_translations:
                    merged = dict(existing.translations or {})
                    for lang, fields in prod_translations.items():
                        merged.setdefault(lang, {}).update(fields)
                    existing.translations = merged
                if _has(row, "price"):
                    existing.base_price = base_price
                # Only re-home the product when the export actually named a
                # category; an absent column must not uncategorise it.
                if _has(row, "category_reference"):
                    existing.category_id = category_id
                # Never implicitly reactivate a soft-deleted product: an absent
                # or blank ``is_active`` column leaves the current state alone.
                if _has(row, "is_active"):
                    existing.is_active = is_active
                if _has(row, "is_stock_product"):
                    existing.is_stock_product = is_stock_product
                if stock_quantity is not None:
                    existing.stock_quantity = stock_quantity
                if calories is not None:
                    existing.calories = calories
                if prep_time is not None:
                    existing.preparation_time = prep_time
                if "cost" in row and str(row["cost"] or "").strip():
                    existing.cost = cost
                if "barcode" in row:
                    existing.barcode = barcode
                if "display_order" in row and str(row["display_order"] or "").strip():
                    existing.display_order = display_order
                for field in (
                    "is_featured",
                    "is_sold_by_weight",
                ):
                    if field in row and str(row[field] or "").strip():
                        setattr(existing, field, _parse_bool(row[field]))
                if image_url:
                    existing.image_urls = [image_url]
                    result.image_urls.append(image_url)
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
                    stock_quantity=stock_quantity or 0,
                    calories=calories,
                    preparation_time=prep_time,
                    cost=cost,
                    barcode=barcode,
                    display_order=display_order,
                    is_featured=_parse_bool(row.get("is_featured", "false")),
                    is_sold_by_weight=_parse_bool(
                        row.get("is_sold_by_weight", "false")
                    ),
                    image_urls=[image_url] if image_url else [],
                )
                db.add(product)
                if image_url:
                    result.image_urls.append(image_url)
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
            mod_translations = _extract_translations(row, ["name"])

            res = await db.execute(
                select(Modifier).where(Modifier.reference == reference)
            )
            existing = res.scalar_one_or_none()

            if existing:
                existing.name = name
                if mod_translations:
                    merged = dict(existing.translations or {})
                    for lang, fields in mod_translations.items():
                        merged.setdefault(lang, {}).update(fields)
                    existing.translations = merged
                if "is_active" in row and str(row["is_active"] or "").strip():
                    existing.is_active = _parse_bool(row["is_active"])
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
                    is_active=_parse_bool(row.get("is_active", "true")),
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
            opt_translations = _extract_translations(row, ["name"])
            price = _parse_decimal(row.get("price", "0"))
            cost = (
                _parse_decimal(row.get("cost", "0"))
                if str(row.get("cost") or "").strip()
                else None
            )
            is_active = _parse_bool(row.get("is_active", "1"))
            display_order = _parse_int(row.get("display_order", "0"))
            calories = (
                _parse_int(row.get("calories", "0"))
                if str(row.get("calories") or "").strip()
                else None
            )

            res = await db.execute(
                select(ModifierOption).where(ModifierOption.sku == sku)
            )
            existing = res.scalar_one_or_none()

            if existing:
                existing.name = name
                if opt_translations:
                    merged = dict(existing.translations or {})
                    for lang, fields in opt_translations.items():
                        merged.setdefault(lang, {}).update(fields)
                    existing.translations = merged
                if _has(row, "price"):
                    existing.price = price
                if "cost" in row and str(row["cost"] or "").strip():
                    existing.cost = cost
                if "calories" in row and str(row["calories"] or "").strip():
                    existing.calories = calories
                # As with products, an absent/blank ``is_active`` must not
                # silently reactivate a retired option.
                if _has(row, "is_active"):
                    existing.is_active = is_active
                if "display_order" in row and str(row["display_order"] or "").strip():
                    existing.display_order = display_order
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
                    cost=cost,
                    calories=calories,
                    is_active=is_active,
                    display_order=display_order,
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
            display_order = _parse_int(row.get("display_order", "0"))

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
                if "display_order" in row and str(row["display_order"] or "").strip():
                    existing.display_order = display_order
                result.updated += 1
            else:
                pm = ProductModifier(
                    product_id=product.id,
                    modifier_id=modifier.id,
                    minimum_options=min_opts,
                    maximum_options=max_opts,
                    free_options=free_opts,
                    unique_options=unique_opts,
                    display_order=display_order,
                )
                db.add(pm)
                result.created += 1

        except Exception as e:
            result.errors.append(ImportError(row=i, message=str(e)))
            result.skipped += 1

    await db.flush()
    return result


async def import_inventory_items(db: AsyncSession, rows: list[dict]) -> ImportResult:
    """Upsert stock catalogue rows from MM's own export.

    Inventory is operational data, so this deliberately resolves an existing row by
    its immutable MM id or SKU only.  Names are included for people, never as a
    matching key; two similarly named ingredients must remain independently safe to
    edit in a spreadsheet.
    """
    result = ImportResult()
    allowed_kinds = {
        "raw_material",
        "packaging",
        "semi_finished",
        "produced_good",
        "resale_good",
    }
    allowed_tracking = {"stocked", "phantom"}

    for row_number, row in enumerate(rows, start=1):
        try:
            item_id = _uuid_or_error(row.get("id"), "id") if row.get("id") else None
            sku = str(row.get("sku") or "").strip()
            name = str(row.get("name") or "").strip()
            if not sku or not name:
                raise ValueError("Missing sku or name")

            by_id = await db.get(InventoryItem, item_id) if item_id else None
            by_sku = await db.scalar(
                select(InventoryItem).where(InventoryItem.sku == sku)
            )
            if by_id is not None and by_sku is not None and by_id.id != by_sku.id:
                raise ValueError("id and sku identify different inventory items")
            existing = by_id or by_sku

            category_id = None
            category_reference = str(row.get("category_reference") or "").strip()
            if category_reference:
                category_id = await db.scalar(
                    select(InventoryCategory.id).where(
                        InventoryCategory.reference == category_reference
                    )
                )
                if category_id is None:
                    raise ValueError(
                        f"Inventory category reference '{category_reference}' was not found"
                    )

            kind = str(row.get("kind") or "raw_material").strip()
            tracking_mode = str(row.get("tracking_mode") or "stocked").strip()
            if kind not in allowed_kinds:
                raise ValueError(f"Unknown kind '{kind}'")
            if tracking_mode not in allowed_tracking:
                raise ValueError(f"Unknown tracking_mode '{tracking_mode}'")
            storage_unit = str(row.get("storage_unit") or "").strip()
            ingredient_unit = str(row.get("ingredient_unit") or "").strip()
            if not storage_unit or not ingredient_unit:
                raise ValueError("storage_unit and ingredient_unit are required")
            factor = _required_decimal(row, "storage_to_ingredient_factor", row_number)
            cost = _parse_decimal(row.get("cost", "0"))
            yield_percentage = _required_decimal(row, "yield_percentage", row_number)
            if yield_percentage > 1:
                raise ValueError("yield_percentage must not exceed 1")

            values = {
                "sku": sku,
                "name": name,
                "barcode": str(row.get("barcode") or "").strip() or None,
                "category_id": category_id,
                "kind": kind,
                "tracking_mode": tracking_mode,
                "storage_unit": storage_unit,
                "ingredient_unit": ingredient_unit,
                "storage_to_ingredient_factor": factor,
                "minimum_level": _parse_decimal(row.get("minimum_level", "0")),
                "maximum_level": _parse_decimal(row.get("maximum_level", "0")),
                "par_level": _parse_decimal(row.get("par_level", "0")),
                "cost": cost,
                "costing_method": str(row.get("costing_method") or "fixed").strip(),
                "yield_percentage": yield_percentage,
                "is_product": _parse_bool(row.get("is_product", "false")),
                "storage_zone": str(row.get("storage_zone") or "").strip() or None,
                "count_order": _parse_int(row.get("count_order", "0")),
                "is_active": _parse_bool(row.get("is_active", "true")),
            }
            if values["cost"] < 0:
                raise ValueError("cost must not be negative")
            if values["costing_method"] not in {"fixed", "from_ingredients"}:
                raise ValueError("costing_method must be fixed or from_ingredients")

            if existing is None:
                db.add(InventoryItem(id=item_id or uuid.uuid4(), **values))
                result.created += 1
            else:
                for field, value in values.items():
                    setattr(existing, field, value)
                result.updated += 1
        except (ValueError, TypeError) as exc:
            result.errors.append(ImportError(row=row_number, message=str(exc)))
            result.skipped += 1

    await db.flush()
    return result


async def import_recipes(db: AsyncSession, rows: list[dict]) -> ImportResult:
    """Stage recipe spreadsheet edits as drafts, never as live recipe changes.

    One owner occupies one contiguous or non-contiguous group in the CSV. Every
    group is validated before any draft is rewritten, which makes an operator's
    corrected export safe to retry after addressing a reported row error.
    """
    result = ImportResult()
    groups: dict[tuple[str, uuid.UUID], list[tuple[int, dict]]] = {}
    errors: list[ImportError] = []
    allowed_kinds = {kind.value for kind in RecipeOwnerKindEnum}

    for row_number, row in enumerate(rows, start=1):
        try:
            owner_kind = str(row.get("owner_kind") or "").strip()
            if owner_kind not in allowed_kinds:
                raise ValueError(
                    "owner_kind must be product, modifier_option, or inventory_item"
                )
            owner_id = _uuid_or_error(row.get("owner_id"), "owner_id")
            owner_model = {
                RecipeOwnerKindEnum.PRODUCT.value: Product,
                RecipeOwnerKindEnum.MODIFIER_OPTION.value: ModifierOption,
                RecipeOwnerKindEnum.INVENTORY_ITEM.value: InventoryItem,
            }[owner_kind]
            if await db.get(owner_model, owner_id) is None:
                raise ValueError("owner_id does not exist for owner_kind")
            ingredient_id = _uuid_or_error(
                row.get("ingredient_item_id"), "ingredient_item_id"
            )
            ingredient = await db.get(InventoryItem, ingredient_id)
            if ingredient is None:
                raise ValueError("ingredient_item_id does not exist")
            _required_decimal(row, "quantity", row_number)
            yield_percentage = _required_decimal(row, "yield_percentage", row_number)
            if yield_percentage > 1:
                raise ValueError("yield_percentage must not exceed 1")
            groups.setdefault((owner_kind, owner_id), []).append((row_number, row))
        except (ValueError, TypeError) as exc:
            errors.append(ImportError(row=row_number, message=str(exc)))

    # Do not apply half a spreadsheet. A duplicate line or a Foodics-review draft
    # must be resolved before any owner draft is replaced.
    for (owner_kind, owner_id), group in groups.items():
        seen: set[uuid.UUID] = set()
        for row_number, row in group:
            ingredient_id = _uuid_or_error(
                row.get("ingredient_item_id"), "ingredient_item_id"
            )
            if ingredient_id in seen:
                errors.append(
                    ImportError(
                        row=row_number,
                        message="duplicate ingredient_item_id in this recipe",
                    )
                )
            seen.add(ingredient_id)
        existing = await recipe_service.get_recipe(db, owner_kind, owner_id)
        draft = next(
            (
                version
                for version in (existing.versions if existing else [])
                if version.status == "draft"
            ),
            None,
        )
        if draft is not None and draft.source != "mm":
            errors.extend(
                ImportError(
                    row=group_row_number,
                    message="recipe has a draft from another import or review batch",
                )
                for group_row_number, _ in group
            )

    if errors:
        result.errors = errors
        result.skipped = len(errors)
        return result

    for (owner_kind, owner_id), group in groups.items():
        try:
            lines = []
            for row_number, row in group:
                ingredient_id = _uuid_or_error(
                    row.get("ingredient_item_id"), "ingredient_item_id"
                )
                inactive_types = [
                    value.strip()
                    for value in str(row.get("inactive_in_order_types") or "").split(
                        "|"
                    )
                    if value.strip()
                ]
                lines.append(
                    recipe_service.RecipeLineInput(
                        item_id=ingredient_id,
                        quantity=_required_decimal(row, "quantity", row_number),
                        yield_percentage=_required_decimal(
                            row, "yield_percentage", row_number
                        ),
                        inactive_in_order_types=inactive_types,
                        display_order=_parse_int(row.get("display_order", "0")),
                        source_metadata={"bulk_import_row": row_number},
                    )
                )
            await recipe_service.create_draft(
                db,
                kind=owner_kind,
                owner_id=owner_id,
                lines=lines,
                source="mm",
                source_metadata={"import": "recipes-workbook"},
            )
            result.updated += 1
        except Exception as exc:  # noqa: BLE001 - turn a bad group into spreadsheet feedback
            for row_number, _ in group:
                result.errors.append(ImportError(row=row_number, message=str(exc)))
                result.skipped += 1

    await db.flush()
    return result
