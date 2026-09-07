"""Schema/service contracts for shift reports that once drifted into 500s.

- F-INV-5: a template item ``required_input`` the schema accepts but the poster
  cannot map to a transaction type raised on submit and blocked the till.
- F-INV-6: ``recipe_path`` is one shape everywhere — a list of hop-lists — so the
  shared type is ``list[list[dict[str, str]]]`` rather than a widened ``list[Any]``.
"""

from __future__ import annotations

import typing
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.inventory_v2 import (
    OrderInventoryMovementLine,
    ReportTemplateItemInput,
)
from app.services.inventory import report_service


def test_report_inputs_all_have_a_posting_type():
    """Every required_input the schema accepts must be one post_report can post."""
    literal_args = set(
        typing.get_args(
            ReportTemplateItemInput.model_fields["required_input"].annotation
        )
    )
    assert literal_args == report_service.supported_report_item_inputs()
    # And 'production' — which had no posting type — is gone from both.
    assert "production" not in literal_args


def _movement_line(recipe_path):
    return OrderInventoryMovementLine(
        item_id=uuid4(),
        item_name="Flour",
        item_sku="FLR-1",
        unit="g",
        signed_quantity="-100",
        balance_after_quantity="900",
        recipe_version_id=None,
        recipe_path=recipe_path,
    )


def test_recipe_path_accepts_hop_lists():
    line = _movement_line([[{"owner_kind": "product", "owner_id": str(uuid4())}]])
    assert line.recipe_path[0][0]["owner_kind"] == "product"
    # Empty is valid — a plain movement carries no expansion.
    assert _movement_line([]).recipe_path == []


def test_recipe_path_rejects_the_retired_flat_dict_shape():
    with pytest.raises(ValidationError):
        _movement_line([{"owner_id": str(uuid4())}])
