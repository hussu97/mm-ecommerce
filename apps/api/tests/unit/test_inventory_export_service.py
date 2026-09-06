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
        cost=Decimal("24.50"),
        costing_method="moving_average",
        yield_percentage=Decimal("100"),
        minimum_level=Decimal("2"),
        par_level=Decimal("5"),
        maximum_level=Decimal("10"),
        is_product=False,
        storage_zone="Chiller",
        count_order=10,
        is_active=True,
    )

    content = await export_service.export_inventory_items(_Db([item]))

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
            "cost": "24.50",
            "costing_method": "moving_average",
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
