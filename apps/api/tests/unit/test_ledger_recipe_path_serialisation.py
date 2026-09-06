"""Regression: the ledger response must accept the recipe_path shape the poster writes.

`consumption_from_orders` movements store `recipe_path` as the recipe *paths* the
expansion walked — a list of paths, each path a list of hop dicts. The response
schema once declared `list[dict]`, so every such row failed validation and the
whole `GET /inventory/transactions` endpoint 500'd, which is why the admin ledger
rendered empty. Pin the nested shape so it cannot regress.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.schemas.inventory import TransactionLineResponse
from app.schemas.inventory_v2 import OrderInventoryMovementLine

_NESTED_PATHS = [
    [{"item_id": str(uuid4())}, {"item_id": str(uuid4())}],
    [{"item_id": str(uuid4())}],
]


def test_transaction_line_accepts_nested_recipe_paths():
    line = TransactionLineResponse.model_validate(
        {
            "id": uuid4(),
            "item_id": uuid4(),
            "quantity": Decimal("1"),
            "unit": "kg",
            "conversion_factor": Decimal("1"),
            "quantity_in_ingredient_unit": Decimal("1"),
            "unit_cost": Decimal("1"),
            "total_cost": Decimal("1"),
            "expected_quantity": None,
            "notes": None,
            "recipe_path": _NESTED_PATHS,
        }
    )
    assert line.recipe_path == _NESTED_PATHS


def test_order_movement_line_accepts_nested_recipe_paths():
    line = OrderInventoryMovementLine.model_validate(
        {
            "item_id": uuid4(),
            "item_name": "Flour",
            "item_sku": "FLR",
            "unit": "kg",
            "signed_quantity": Decimal("-2"),
            "balance_after_quantity": Decimal("10"),
            "recipe_version_id": None,
            "recipe_path": _NESTED_PATHS,
        }
    )
    assert line.recipe_path == _NESTED_PATHS
