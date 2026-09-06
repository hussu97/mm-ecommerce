"""Recipe workbook parsing keeps lookup/reference sheets out of import data."""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi import UploadFile
from openpyxl import Workbook

from app.api.v1.import_data import _parse_recipe_upload


def _recipe_workbook() -> tuple[bytes, str, str]:
    owner_id = str(uuid.uuid4())
    ingredient_id = str(uuid.uuid4())
    workbook = Workbook()
    recipes = workbook.active
    recipes.title = "Recipes"
    recipes.append(
        [
            "owner_kind",
            "owner_id",
            "ingredient_item_id",
            "quantity",
            "yield_percentage",
            "display_order",
        ]
    )
    recipes.append(["product", owner_id, ingredient_id, "2.5", "1", 0])

    reference = workbook.create_sheet("Owner reference")
    reference.append(["owner_kind", "owner_id", "owner_sku"])
    # This deliberately invalid reference must never reach recipe validation.
    reference.append(["not-a-recipe-owner", "not-a-uuid", "READ-ONLY"])

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue(), owner_id, ingredient_id


@pytest.mark.asyncio
async def test_recipe_workbook_import_reads_only_the_editable_recipes_sheet():
    content, owner_id, ingredient_id = _recipe_workbook()
    upload = UploadFile(filename="recipes.xlsx", file=io.BytesIO(content))

    rows = await _parse_recipe_upload(upload)

    assert rows == [
        {
            "owner_kind": "product",
            "owner_id": owner_id,
            "ingredient_item_id": ingredient_id,
            "quantity": "2.5",
            "yield_percentage": "1",
            "display_order": 0,
        }
    ]


@pytest.mark.asyncio
async def test_recipe_import_keeps_csv_uploads_compatible():
    upload = UploadFile(
        filename="recipes.csv",
        file=io.BytesIO(b"owner_kind,owner_id\nproduct,owner-uuid\n"),
    )

    assert await _parse_recipe_upload(upload) == [
        {"owner_kind": "product", "owner_id": "owner-uuid"}
    ]
