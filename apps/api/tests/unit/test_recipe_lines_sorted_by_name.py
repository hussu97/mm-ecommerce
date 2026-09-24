"""Recipe lines are listed by ingredient name wherever a recipe is shown."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.schemas.inventory_v2 import RecipeVersionResponse


def _line(name: str, display_order: int) -> dict:
    return {
        "id": uuid.uuid4(),
        "item_id": uuid.uuid4(),
        "item_name": name,
        "quantity": Decimal("1"),
        "ingredient_unit": "g",
        "yield_percentage": Decimal("1"),
        "inactive_in_order_types": [],
        "display_order": display_order,
        "source_metadata": {},
    }


def test_version_lines_come_back_by_ingredient_name_case_insensitively():
    now = datetime.now(UTC)
    version = RecipeVersionResponse.model_validate(
        {
            "id": uuid.uuid4(),
            "recipe_id": uuid.uuid4(),
            "version_number": 1,
            "status": "active",
            "basis": "batch",
            "batch_yield": Decimal("10"),
            "source": "mm",
            "source_payload_hash": None,
            "source_metadata": {},
            "activated_at": now,
            "activated_by": None,
            "retired_at": None,
            "created_at": now,
            "updated_at": now,
            "lines": [
                _line("Sugar", 0),
                _line("butter", 1),
                _line("Eggs", 2),
                _line("Brown Sugar", 3),
            ],
        }
    )
    assert [line.item_name for line in version.lines] == [
        "Brown Sugar",
        "butter",
        "Eggs",
        "Sugar",
    ]
