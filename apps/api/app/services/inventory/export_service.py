from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, with_loader_criteria

from app.models.category import Category
from app.models.inventory import InventoryItem, PurchaseOrder
from app.models.inventory_v2 import Recipe, RecipeVersion, RecipeVersionStatusEnum
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.order import Order, OrderStatusEnum
from app.models.product import Product
from app.services.inventory import cost_layer_service, po_misc_service, recipe_service

__all__ = [
    "export_categories",
    "export_inventory_items",
    "export_modifier_options",
    "export_modifiers",
    "export_orders",
    "export_product_modifiers",
    "export_products",
    "export_purchase_orders_workbook",
    "export_recipes",
    "export_recipes_workbook",
]


RECIPE_EXPORT_HEADERS = [
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
RECIPE_OWNER_REFERENCE_HEADERS = ["owner_kind", "owner_id", "owner_sku", "owner_name"]
_WORKBOOK_HEADER_FILL = PatternFill("solid", fgColor="2D241E")
_WORKBOOK_HEADER_FONT = Font(color="FFFFFF", bold=True)

#: The leading characters Excel and Google Sheets read as the start of a formula.
#: A cell beginning with one of these is executed on open, so an item name typed
#: as `=cmd|'/c calc'!A1` becomes code the moment an operator opens the file.
_FORMULA_INJECTION_PREFIXES = frozenset("=+-@\t\r")


def _safe(value: str | int) -> str | int:
    """Neutralise spreadsheet formula injection on one user-derived cell.

    A value that begins with `=`, `+`, `-`, `@`, a tab or a carriage return is
    prefixed with a single quote so a spreadsheet treats it as text rather than
    executing it (F-INV-19). This mirrors the admin-side `csvCell()` guard that
    already protects the browser-built counts CSV; every column here is filled
    from catalogue names, SKUs, references and descriptions that an operator can
    type, so each one is attacker-influenced data.

    Integers pass through untouched — they cannot open a formula, and keeping
    them numeric preserves both the workbook's numeric cells and the round-trip
    back through the importer. The `csv`/`openpyxl` writers already handle quote
    doubling and escaping, so this only ever adds the leading quote.
    """
    if isinstance(value, str) and value[:1] in _FORMULA_INJECTION_PREFIXES:
        return f"'{value}"
    return value


def _safe_row(row: Sequence[str | int]) -> list[str | int]:
    """`_safe` across a whole data row, ready to hand to a writer."""
    return [_safe(cell) for cell in row]


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
        w.writerow(_safe_row(row_data))
    return buf.getvalue()


async def export_products(db: AsyncSession, languages: list[str]) -> str:
    result = await db.execute(
        select(Product)
        .options(joinedload(Product.category))
        .order_by(Product.display_order)
    )
    rows = result.scalars().unique().all()

    # Product cost is the live recipe cost now (the stale `Product.cost` column was
    # dropped). Load the active recipe graph once and price each product from it;
    # a product with no recipe exports an empty cost cell.
    catalog = await recipe_service.load_active_catalog(db)

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
            "labels",
            "is_sold_by_weight",
        ]
    )
    w.writerow(header)
    # One ingredient-cost query for the whole export, not one per product.
    product_costs = await recipe_service.owner_unit_costs(
        db, [("product", r.id) for r in rows], catalog=catalog
    )
    for r in rows:
        category_ref = (
            r.category.reference if r.category and r.category.reference else ""
        )
        image = r.image_urls[0] if r.image_urls else ""
        t = r.translations or {}
        product_cost = product_costs.get(("product", r.id))
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
                str(product_cost) if product_cost is not None else "",
                r.barcode or "",
                str(r.display_order),
                ";".join(r.labels or []),
                str(r.is_sold_by_weight),
            ]
        )
        w.writerow(_safe_row(row_data))
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
        w.writerow(_safe_row(row_data))
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
        w.writerow(_safe_row(row_data))
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
            _safe_row(
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
            _safe_row(
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
    # Cost is FIFO now: export the item's current derived cost (read-only), not a
    # stored column. The importer ignores this column, so it round-trips safely.
    costs = await cost_layer_service.item_average_costs(db, [item.id for item in rows])
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
            "average_cost",
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
            _safe_row(
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
                    str(costs.get(item.id, Decimal("0"))),
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
        )
    return buf.getvalue()


async def _recipe_export_rows(
    db: AsyncSession,
) -> tuple[list[list[str | int]], list[list[str]]]:
    """Return the editable recipe rows and every valid recipe-owner reference.

    A draft takes precedence over the active version so a downloaded file is an
    honest representation of what an operator would edit next. Importing this
    file only stages drafts; activation is intentionally kept in the owner UI.
    """
    recipes = (
        (
            await db.execute(
                select(Recipe).options(
                    joinedload(Recipe.versions).joinedload(RecipeVersion.lines),
                    # A workbook exposes the current editable state only; loading
                    # every retired historical version makes export cost grow with
                    # recipe age while producing no editable row.
                    with_loader_criteria(
                        RecipeVersion,
                        RecipeVersion.status.in_(
                            [
                                RecipeVersionStatusEnum.DRAFT.value,
                                RecipeVersionStatusEnum.ACTIVE.value,
                            ]
                        ),
                        include_aliases=True,
                    ),
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    # The reference worksheet must include owners that do not have a recipe
    # yet; those are exactly the rows an operator needs when building a first
    # import. These are four bounded catalogue queries, not per-recipe lookups.
    products = {
        value.id: value
        for value in (
            await db.execute(select(Product).order_by(Product.sku, Product.name))
        )
        .scalars()
        .all()
    }
    options = {
        value.id: value
        for value in (
            await db.execute(
                select(ModifierOption).order_by(ModifierOption.sku, ModifierOption.name)
            )
        )
        .scalars()
        .all()
    }
    items = {
        value.id: value
        for value in (
            await db.execute(
                select(InventoryItem).order_by(InventoryItem.sku, InventoryItem.name)
            )
        )
        .scalars()
        .all()
    }

    editable_rows: list[list[str | int]] = []
    for recipe in sorted(
        recipes,
        key=lambda value: (
            value.owner_kind,
            str(
                value.product_id
                or value.modifier_option_id
                or value.inventory_item_id
                or value.id
            ),
        ),
    ):
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
        owner = {
            "product": products,
            "modifier_option": options,
            "inventory_item": items,
        }.get(recipe.owner_kind, {}).get(owner_id)
        owner_sku = getattr(owner, "sku", "") or ""
        owner_name = getattr(owner, "name", "") or ""
        for line in sorted(
            version.lines, key=lambda value: (value.display_order, str(value.item_id))
        ):
            ingredient = items.get(line.item_id)
            editable_rows.append(
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

    owner_rows = [
        [kind, str(value.id), getattr(value, "sku", "") or "", value.name]
        for kind, values in (
            ("product", products.values()),
            ("modifier_option", options.values()),
            ("inventory_item", items.values()),
        )
        for value in values
    ]
    return editable_rows, owner_rows


async def export_recipes(db: AsyncSession) -> str:
    """Keep the original CSV export available for existing integrations."""
    editable_rows, _ = await _recipe_export_rows(db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(RECIPE_EXPORT_HEADERS)
    writer.writerows(_safe_row(row) for row in editable_rows)
    return buf.getvalue()


def _write_workbook_sheet(
    sheet,
    headers: list[str],
    rows: Sequence[Sequence[str | int]],
    *,
    protected: bool,
) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = _WORKBOOK_HEADER_FILL
        cell.font = _WORKBOOK_HEADER_FONT
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:{get_column_letter(len(headers))}{max(1, len(rows) + 1)}"
    )
    for row in rows:
        sheet.append(_safe_row(row))
    for column_index, header in enumerate(headers, start=1):
        values = [header, *(str(row[column_index - 1]) for row in rows)]
        sheet.column_dimensions[get_column_letter(column_index)].width = min(
            max(len(value) for value in values) + 2, 36
        )
    if protected:
        # Excel sheet protection is an editing guard, not a security boundary.
        # The importer independently selects only the editable worksheet.
        sheet.protection.sheet = True


PURCHASE_ORDER_EXPORT_HEADERS = [
    "reference",
    "supplier",
    "status",
    "business_date",
    "delivery_date",
    "invoice_reference",
    "invoice_attached",
    "item_lines",
    "misc_lines",
    "net",
    "vat",
    "total_gross",
    "additional_cost",
    "total_cost",
]

#: The lines sheet: one row per line (inventory OR misc), carrying the PO header
#: fields so each row is self-contained, then the line's own detail, then the
#: PO-level totals for context.
PURCHASE_ORDER_LINE_EXPORT_HEADERS = [
    "po_reference",
    "supplier",
    "status",
    "business_date",
    "delivery_date",
    "invoice_reference",
    "invoice_attached",
    "line_type",
    "item_name",
    "sku",
    "quantity",
    "storage_unit",
    "unit_cost",
    "net",
    "vat",
    "gross",
    "category",
    "period_from",
    "period_to",
    "po_additional_cost",
    "po_net",
    "po_vat",
    "po_total_gross",
    "po_total_cost",
]


def _po_sort_key(order: PurchaseOrder) -> tuple[date, str]:
    """Delivery date ascending, undated last, then by reference for stability."""
    return (order.delivery_date or date.max, order.reference or "")


def export_purchase_orders_workbook(
    orders: Sequence[PurchaseOrder],
    supplier_names: dict,
    items_lookup: dict | None = None,
    *,
    sees_gated: bool = False,
) -> bytes:
    """Two sheets: one row per purchase order, and one row per line.

    Both are sorted by delivery date ascending (undated orders last). The lines
    sheet splits inventory lines from miscellaneous (non-inventory) lines with a
    ``line_type`` column, and repeats the PO header fields and totals on each row
    so a line stands alone. Money columns are written as numbers so the operator
    can sum them; text passes through ``_safe`` in the shared sheet writer.
    ``orders``, their items and misc_items are already loaded by the caller.

    A viewer who may not see admin-only misc categories (``sees_gated`` false)
    gets the workbook without those lines and with totals that exclude them,
    exactly as the PO screens show it (``po_misc_service.visible_misc``)."""
    items_lookup = items_lookup or {}
    ordered = sorted(orders, key=_po_sort_key)

    header_rows: list[list[str | int | float]] = []
    line_rows: list[list[str | int | float]] = []
    for order in ordered:
        view = po_misc_service.visible_misc(order, sees_gated=sees_gated)
        supplier = supplier_names.get(order.supplier_id, "")
        status = order.status.replace("_", " ")
        delivery = order.delivery_date.isoformat() if order.delivery_date else ""
        invoice_ref = order.supplier_reference or ""
        invoice_attached = "Yes" if order.invoice_object_key else "No"
        header_rows.append(
            [
                order.reference,
                supplier,
                status,
                order.business_date or "",
                delivery,
                invoice_ref,
                invoice_attached,
                len(order.items),
                len(view.misc_items),
                float(view.subtotal_net),
                float(view.vat_total),
                float(view.total_gross),
                float(order.additional_cost or 0),
                float(view.total_cost),
            ]
        )

        # The PO header block repeated on every line row of this order.
        po_prefix: list[str | int | float] = [
            order.reference,
            supplier,
            status,
            order.business_date or "",
            delivery,
            invoice_ref,
            invoice_attached,
        ]
        po_totals: list[str | int | float] = [
            float(order.additional_cost or 0),
            float(view.subtotal_net),
            float(view.vat_total),
            float(view.total_gross),
            float(view.total_cost),
        ]
        for line in order.items:
            item = items_lookup.get(line.item_id)
            line_rows.append(
                po_prefix
                + [
                    "Inventory item",
                    item.name if item else "",
                    (item.sku or "") if item else "",
                    float(line.quantity or 0),
                    (item.storage_unit or "") if item else "",
                    float(line.unit_cost or 0),
                    float(line.net_total or 0),
                    float(line.vat_amount or 0),
                    float(line.entered_total or 0),
                    "",
                    "",
                    "",
                ]
                + po_totals
            )
        for misc in view.misc_items:
            line_rows.append(
                po_prefix
                + [
                    "Miscellaneous",
                    misc.name,
                    "",
                    float(misc.quantity or 0),
                    misc.storage_unit or "",
                    float(misc.unit_cost or 0),
                    float(misc.net_total or 0),
                    float(misc.vat_amount or 0),
                    float(misc.entered_total or 0),
                    misc.category_name or "",
                    misc.period_from.isoformat() if misc.period_from else "",
                    misc.period_to.isoformat() if misc.period_to else "",
                ]
                + po_totals
            )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Purchase orders"
    _write_workbook_sheet(
        sheet, PURCHASE_ORDER_EXPORT_HEADERS, header_rows, protected=False
    )
    lines_sheet = workbook.create_sheet("Lines")
    _write_workbook_sheet(
        lines_sheet, PURCHASE_ORDER_LINE_EXPORT_HEADERS, line_rows, protected=False
    )
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


async def export_recipes_workbook(db: AsyncSession) -> bytes:
    """Export editable recipes beside a guarded, reference-only owner catalogue."""
    editable_rows, owner_rows = await _recipe_export_rows(db)
    workbook = Workbook()
    recipes_sheet = workbook.active
    recipes_sheet.title = "Recipes"
    _write_workbook_sheet(
        recipes_sheet, RECIPE_EXPORT_HEADERS, editable_rows, protected=False
    )
    owners_sheet = workbook.create_sheet("Owner reference")
    _write_workbook_sheet(
        owners_sheet,
        RECIPE_OWNER_REFERENCE_HEADERS,
        owner_rows,
        protected=True,
    )
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


_COST_SUMMARY_HEADERS = [
    "Category",
    "Product",
    "SKU",
    "Modifier",
    "Option",
    "Price (AED)",
    "Cost (AED)",
    "Cost % of price",
    "Note",
]
_COST_RECIPE_HEADERS = [
    "Product",
    "Modifier",
    "Option",
    "Inventory item",
    "Item SKU",
    "Item kind",
    "Qty",
    "Unit",
    "Unit cost (AED)",
    "Line cost (AED)",
]
_MONEY_FORMAT = "#,##0.00;(#,##0.00);-"


def _cost_sheet(sheet, headers: list[str], widths: list[int]) -> None:
    body = Font(name="Arial", size=10)
    head = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="5B3A29")
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row in sheet.iter_rows():
        for cell in row:
            cell.font = body
    for cell in sheet[1]:
        cell.font = head
        cell.fill = fill
    sheet.freeze_panes = "A2"
    if len(headers) > 2:
        sheet.auto_filter.ref = sheet.dimensions


async def export_product_costs_workbook(db: AsyncSession, *, as_of: date) -> bytes:
    """
    Every active product's recipe cost against its price, and the recipe
    behind it — the console's Price / Cost / Cost % columns as a workbook.

    Tab 1 lists each product (or, for a product with options, each option at
    base + option price, costed with the product's own recipe); tab 2 the leaf
    ingredients of each product's own recipe and each option's, sorted by
    product, modifier, option and ingredient. Cost % and line cost are live
    formulas. Both tabs come from one pricing pass
    (`product_cost_service.catalogue_report`).
    """
    from app.services.catalog import product_cost_service

    report = await product_cost_service.catalogue_report(db)
    products = {p.id: p for p in report.products}
    workbook = Workbook()

    summary = workbook.active
    summary.title = "Cost summary"
    summary.append(_COST_SUMMARY_HEADERS)
    rows = []
    for product in report.products:
        cost = report.costs[product.id]
        category = product.category.name if product.category else ""
        base = [category, product.name, product.sku or ""]
        if cost.options:
            note = (
                "Product has no recipe of its own; option recipe only"
                if cost.missing_recipe
                else ""
            )
            for option in cost.options:
                rows.append(
                    (
                        *base,
                        option.modifier_name,
                        option.name,
                        option.price,
                        option.cost,
                        note if option.cost is not None else "No recipe",
                    )
                )
        else:
            if not cost.consumes_stock:
                note = "Does not track stock"
            elif cost.cost is None:
                note = "No recipe"
            else:
                note = ""
            rows.append((*base, "", "", cost.price, cost.cost, note))
    rows.sort(key=lambda r: (r[0].lower(), r[1].lower(), r[3].lower(), r[5], r[4]))
    for category, name, sku, modifier, option, price, cost, note in rows:
        summary.append(
            [
                category,
                name,
                sku,
                modifier,
                option,
                float(price),
                None if cost is None else float(cost),
                None,
                note,
            ]
        )
        i = summary.max_row
        summary.cell(i, 8).value = f'=IF(AND(ISNUMBER(G{i}),F{i}>0),G{i}/F{i},"")'
        summary.cell(i, 6).number_format = _MONEY_FORMAT
        summary.cell(i, 7).number_format = _MONEY_FORMAT
        summary.cell(i, 8).number_format = "0.0%"
    _cost_sheet(summary, _COST_SUMMARY_HEADERS, [16, 40, 10, 18, 22, 12, 12, 15, 44])

    recipes = workbook.create_sheet("Recipes")
    recipes.append(_COST_RECIPE_HEADERS)
    lines = sorted(
        report.recipe_lines,
        key=lambda r: (
            products[r.product_id].name.lower(),
            (r.modifier_name or "").lower(),
            (r.option_name or "").lower(),
            (r.item_name or "").lower(),
        ),
    )
    for line in lines:
        recipes.append(
            [
                products[line.product_id].name,
                line.modifier_name or "(product recipe)",
                line.option_name or "",
                line.item_name or "(no active recipe)",
                line.item_sku or "",
                (line.item_kind or "").replace("_", " "),
                None if line.quantity is None else float(line.quantity),
                line.unit or "",
                None if line.unit_cost is None else float(line.unit_cost),
                None,
            ]
        )
        i = recipes.max_row
        recipes.cell(
            i, 10
        ).value = f'=IF(AND(ISNUMBER(G{i}),ISNUMBER(I{i})),G{i}*I{i},"")'
        recipes.cell(i, 7).number_format = "#,##0.####"
        recipes.cell(i, 9).number_format = "#,##0.0000"
        recipes.cell(i, 10).number_format = "#,##0.0000"
    _cost_sheet(recipes, _COST_RECIPE_HEADERS, [40, 18, 22, 34, 11, 14, 10, 10, 15, 15])

    notes = workbook.create_sheet("Notes")
    notes.append(["Topic", "Detail"])
    for topic, detail in (
        ("As of", as_of.isoformat()),
        (
            "Items",
            "Every active product, all categories. Inactive options are left out.",
        ),
        (
            "Cost basis",
            "Current recipe cost: each ingredient at its current FIFO average "
            "cost across all warehouses (last known cost if out of stock). "
            "Purchase prices are gross (VAT-inclusive), as booked.",
        ),
        (
            "Options",
            "For a product with options, Price = base price + option price and "
            "Cost = the product's own recipe + the option's (what a sale "
            "consumes). On Recipes the product's own recipe is the "
            '"(product recipe)" rows.',
        ),
        (
            "Blank cost",
            "No active recipe, so the cost is unknown (not zero). A cost of 0 on "
            "a resale item means it has never been priced.",
        ),
        (
            "Same figures as",
            "The Price / Cost / Cost % columns on the admin product list.",
        ),
    ):
        notes.append([topic, detail])
    _cost_sheet(notes, ["Topic", "Detail"], [18, 110])

    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()
