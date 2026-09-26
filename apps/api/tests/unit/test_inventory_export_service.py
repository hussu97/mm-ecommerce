"""Regression coverage for the editable inventory catalogue export."""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.services.inventory import export_service


class _Result:
    def __init__(self, rows: list[object]):
        self.rows = rows

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, *result_sets: list[object]):
        self.result_sets = iter(result_sets)

    async def execute(self, _statement):
        return _Result(next(self.result_sets))


@pytest.mark.asyncio
async def test_inventory_item_export_uses_the_category_reference():
    """The export must eagerly load the real model relationship, not a typo."""
    item = SimpleNamespace(
        id=uuid.uuid4(),
        sku="RM-BUTTER",
        name="Butter",
        barcode=None,
        category=SimpleNamespace(reference="raw-materials"),
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="kg",
        ingredient_unit="g",
        storage_to_ingredient_factor=Decimal("1000"),
        yield_percentage=Decimal("100"),
        minimum_level=Decimal("2"),
        par_level=Decimal("5"),
        maximum_level=Decimal("10"),
        is_product=False,
        storage_zone="Chiller",
        count_order=10,
        is_active=True,
    )

    # Two queries now: the items, then the bulk FIFO cost lookup (no layers here,
    # so the derived cost is 0).
    content = await export_service.export_inventory_items(_Db([item], []))

    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows == [
        {
            "id": str(item.id),
            "sku": "RM-BUTTER",
            "name": "Butter",
            "barcode": "",
            "category_reference": "raw-materials",
            "kind": "raw_material",
            "tracking_mode": "stocked",
            "storage_unit": "kg",
            "ingredient_unit": "g",
            "storage_to_ingredient_factor": "1000",
            "average_cost": "0",
            "yield_percentage": "100",
            "minimum_level": "2",
            "par_level": "5",
            "maximum_level": "10",
            "is_product": "False",
            "storage_zone": "Chiller",
            "count_order": "10",
            "is_active": "True",
        }
    ]


@pytest.mark.asyncio
async def test_recipe_workbook_includes_all_valid_owners_in_a_protected_reference_sheet():
    """Operators can build new recipes without guessing an owner UUID."""
    product = SimpleNamespace(id=uuid.uuid4(), sku="BOX-3", name="Box of 3")
    modifier_option = SimpleNamespace(
        id=uuid.uuid4(), sku="ADD-CHOC", name="Add chocolate"
    )
    inventory_item = SimpleNamespace(id=uuid.uuid4(), sku="RM-BUTTER", name="Butter")
    version = SimpleNamespace(
        status="active",
        version_number=1,
        lines=[
            SimpleNamespace(
                item_id=inventory_item.id,
                quantity=Decimal("3"),
                ingredient_unit="g",
                yield_percentage=Decimal("1"),
                inactive_in_order_types=[],
                display_order=0,
            )
        ],
    )
    recipe = SimpleNamespace(
        id=uuid.uuid4(),
        owner_kind="product",
        product_id=product.id,
        modifier_option_id=None,
        inventory_item_id=None,
        versions=[version],
    )

    content = await export_service.export_recipes_workbook(
        _Db([recipe], [product], [modifier_option], [inventory_item])
    )

    workbook = load_workbook(io.BytesIO(content), data_only=True)
    assert workbook.sheetnames == ["Recipes", "Owner reference"]
    assert workbook["Recipes"].protection.sheet is False
    assert workbook["Owner reference"].protection.sheet is True
    assert list(workbook["Recipes"].values) == [
        tuple(export_service.RECIPE_EXPORT_HEADERS),
        (
            "product",
            str(product.id),
            "BOX-3",
            "Box of 3",
            1,
            "active",
            str(inventory_item.id),
            "RM-BUTTER",
            "Butter",
            "3",
            "g",
            "1",
            None,
            0,
        ),
    ]
    assert list(workbook["Owner reference"].values) == [
        tuple(export_service.RECIPE_OWNER_REFERENCE_HEADERS),
        ("product", str(product.id), "BOX-3", "Box of 3"),
        (
            "modifier_option",
            str(modifier_option.id),
            "ADD-CHOC",
            "Add chocolate",
        ),
        ("inventory_item", str(inventory_item.id), "RM-BUTTER", "Butter"),
    ]


def test_purchase_orders_workbook_has_header_and_lines_sheets_sorted_by_delivery():
    """Two sheets — PO level and per-line — both delivery-date ascending, and the
    lines sheet splits inventory from miscellaneous lines with all their money."""
    import datetime

    supplier_id = uuid.uuid4()
    item_id = uuid.uuid4()
    item = SimpleNamespace(id=item_id, name="Butter", sku="RM-1", storage_unit="g")

    later = SimpleNamespace(
        reference="PO-2",
        supplier_id=supplier_id,
        status="closed",
        business_date="2026-09-20",
        delivery_date=datetime.date(2026, 9, 25),
        supplier_reference="INV9",
        invoice_object_key="k",
        subtotal_net=Decimal("100"),
        vat_total=Decimal("5"),
        total_gross=Decimal("105"),
        additional_cost=Decimal("0"),
        total_cost=Decimal("105"),
        items=[
            SimpleNamespace(
                item_id=item_id,
                quantity=Decimal("10"),
                unit="storage",
                unit_cost=Decimal("10.5"),
                net_total=Decimal("100"),
                vat_amount=Decimal("5"),
                entered_total=Decimal("105"),
                total_cost=Decimal("105"),
            )
        ],
        misc_items=[
            SimpleNamespace(
                id=uuid.uuid4(),
                name="Gift wrap",
                quantity=Decimal("2"),
                storage_unit="roll",
                unit_cost=Decimal("10.5"),
                net_total=Decimal("20"),
                vat_amount=Decimal("1"),
                entered_total=Decimal("21"),
                category_name="Cake Supplies",
                category_admin_only=False,
                period_from=datetime.date(2026, 9, 1),
                period_to=datetime.date(2026, 9, 30),
            )
        ],
    )
    earlier = SimpleNamespace(
        reference="PO-1",
        supplier_id=supplier_id,
        status="pending",
        business_date="2026-09-19",
        delivery_date=datetime.date(2026, 9, 22),
        supplier_reference=None,
        invoice_object_key=None,
        subtotal_net=Decimal("50"),
        vat_total=Decimal("0"),
        total_gross=Decimal("50"),
        additional_cost=Decimal("0"),
        total_cost=Decimal("50"),
        items=[],
        misc_items=[],
    )
    undated = SimpleNamespace(
        reference="PO-3",
        supplier_id=supplier_id,
        status="draft",
        business_date="2026-09-18",
        delivery_date=None,
        supplier_reference=None,
        invoice_object_key=None,
        subtotal_net=Decimal("0"),
        vat_total=Decimal("0"),
        total_gross=Decimal("0"),
        additional_cost=Decimal("0"),
        total_cost=Decimal("0"),
        items=[],
        misc_items=[],
    )

    content = export_service.export_purchase_orders_workbook(
        [later, earlier, undated], {supplier_id: "Carrefour"}, {item_id: item}
    )
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    assert workbook.sheetnames == ["Purchase orders", "Lines"]

    header = workbook["Purchase orders"]
    # Delivery date ascending; the undated PO sorts last.
    assert [r[0] for r in header.iter_rows(min_row=2, values_only=True)] == [
        "PO-1",
        "PO-2",
        "PO-3",
    ]

    lines = workbook["Lines"]
    assert list(lines[1][i].value for i in (7, 8, 9, 10, 11, 12, 13, 14, 15)) == [
        "line_type",
        "item_name",
        "sku",
        "quantity",
        "storage_unit",
        "unit_cost",
        "net",
        "vat",
        "gross",
    ]
    body = list(lines.iter_rows(min_row=2, values_only=True))
    # Only PO-2 has lines: one inventory, one misc.
    assert [(r[0], r[7], r[8], r[9]) for r in body] == [
        ("PO-2", "Inventory item", "Butter", "RM-1"),
        ("PO-2", "Miscellaneous", "Gift wrap", None),
    ]
    inv = body[0]
    assert (inv[10], inv[11], inv[13], inv[14], inv[15]) == (10, "g", 100, 5, 105)
    misc = body[1]
    assert (misc[10], misc[11], misc[13], misc[14], misc[15]) == (2, "roll", 20, 1, 21)
    assert list(lines[1][i].value for i in (16, 17, 18)) == [
        "category",
        "period_from",
        "period_to",
    ]
    assert (misc[16], misc[17], misc[18]) == (
        "Cake Supplies",
        "2026-09-01",
        "2026-09-30",
    )
    assert (inv[16], inv[17], inv[18]) == (None, None, None)


def _rent_po():
    import datetime

    rent = SimpleNamespace(
        id=uuid.uuid4(),
        name="October rent",
        quantity=Decimal("1"),
        storage_unit="month",
        unit_cost=Decimal("1000"),
        net_total=Decimal("1000"),
        vat_amount=Decimal("0"),
        entered_total=Decimal("1000"),
        category_name="Rent",
        category_admin_only=True,
        period_from=datetime.date(2026, 10, 1),
        period_to=datetime.date(2026, 10, 31),
    )
    wrap = SimpleNamespace(
        id=uuid.uuid4(),
        name="Gift wrap",
        quantity=Decimal("2"),
        storage_unit="roll",
        unit_cost=Decimal("10.5"),
        net_total=Decimal("20"),
        vat_amount=Decimal("1"),
        entered_total=Decimal("21"),
        category_name="Cake Supplies",
        category_admin_only=False,
        period_from=datetime.date(2026, 10, 1),
        period_to=datetime.date(2026, 10, 31),
    )
    return SimpleNamespace(
        reference="PO-9",
        supplier_id=uuid.uuid4(),
        status="closed",
        business_date="2026-10-01",
        delivery_date=None,
        supplier_reference=None,
        invoice_object_key=None,
        subtotal_net=Decimal("1020"),
        vat_total=Decimal("1"),
        total_gross=Decimal("1021"),
        additional_cost=Decimal("0"),
        total_cost=Decimal("1021"),
        items=[],
        misc_items=[rent, wrap],
    )


def test_purchase_orders_workbook_hides_admin_only_lines_and_their_money():
    """A viewer blind to admin-only categories gets the PO without the rent line,
    and its totals without the rent — the same view the PO screens give."""
    content = export_service.export_purchase_orders_workbook([_rent_po()], {}, {})
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    (header,) = list(workbook["Purchase orders"].iter_rows(min_row=2, values_only=True))
    # misc_lines, net, vat, total_gross, additional_cost, total_cost
    assert header[8:14] == (1, 20, 1, 21, 0, 21)
    body = list(workbook["Lines"].iter_rows(min_row=2, values_only=True))
    assert [r[8] for r in body] == ["Gift wrap"]


def test_purchase_orders_workbook_keeps_admin_only_lines_for_holders():
    content = export_service.export_purchase_orders_workbook(
        [_rent_po()], {}, {}, sees_gated=True
    )
    workbook = load_workbook(io.BytesIO(content), data_only=True)
    (header,) = list(workbook["Purchase orders"].iter_rows(min_row=2, values_only=True))
    assert header[8:14] == (2, 1020, 1, 1021, 0, 1021)
    body = list(workbook["Lines"].iter_rows(min_row=2, values_only=True))
    assert [(r[8], r[16]) for r in body] == [
        ("October rent", "Rent"),
        ("Gift wrap", "Cake Supplies"),
    ]
