"""Regression coverage for the editable inventory catalogue export."""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

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
    def __init__(self, rows: list[object]):
        self.rows = rows

    async def execute(self, _statement):
        return _Result(self.rows)


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
