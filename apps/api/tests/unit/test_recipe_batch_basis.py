"""Batch-basis recipes: lines make a batch of N units, consumed pro-rata.

A batch version's lines are the ingredients for one batch; ``batch_yield`` owner
units come out of it, so drawing M units consumes ``M / batch_yield`` of the
lines. The divisor lives in ``expand_owner`` so every consumption path inherits
it and the ledger/reports stay at ingredient-unit level.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pydantic
import pytest

from app.core.exceptions import BadRequestError
from app.models.inventory import InventoryItem
from app.models.inventory_v2 import (
    InventoryTrackingModeEnum,
    RecipeLine,
    RecipeVersion,
)
from app.services.inventory import recipe_service


def _item(*, tracking: str = "stocked") -> InventoryItem:
    item = InventoryItem(name="ingredient", kind="raw_material")
    item.id = uuid.uuid4()
    item.tracking_mode = tracking
    return item


def _version(*, basis: str, batch_yield, lines: list[RecipeLine]) -> RecipeVersion:
    version = RecipeVersion(basis=basis, batch_yield=batch_yield)
    version.id = uuid.uuid4()
    version.lines = lines
    return version


def _line(item_id: uuid.UUID, quantity: str) -> RecipeLine:
    return RecipeLine(
        item_id=item_id,
        quantity=Decimal(quantity),
        ingredient_unit="g",
        yield_percentage=Decimal("1"),
        display_order=0,
        inactive_in_order_types=[],
    )


# --- _normalise_basis ------------------------------------------------------


def test_unit_basis_drops_any_yield():
    assert recipe_service._normalise_basis("unit", None) == ("unit", None)
    assert recipe_service._normalise_basis("unit", Decimal("5")) == ("unit", None)


def test_batch_basis_requires_positive_yield():
    assert recipe_service._normalise_basis("batch", Decimal("16")) == (
        "batch",
        Decimal("16"),
    )
    with pytest.raises(BadRequestError):
        recipe_service._normalise_basis("batch", None)
    with pytest.raises(BadRequestError):
        recipe_service._normalise_basis("batch", Decimal("0"))


def test_unknown_basis_rejected():
    with pytest.raises(BadRequestError):
        recipe_service._normalise_basis("bogus", None)


# --- expand_owner divisor --------------------------------------------------


async def test_batch_version_divides_consumption_by_yield():
    flour = _item()
    owner_id = uuid.uuid4()
    version = _version(
        basis="batch", batch_yield=Decimal("16"), lines=[_line(flour.id, "2")]
    )
    catalog = recipe_service.ActiveRecipeCatalog(
        versions={("inventory_item", owner_id): version},
        items={flour.id: flour},
    )

    totals, _ = await recipe_service.expand_owner(
        MagicMock(),
        kind="inventory_item",
        owner_id=owner_id,
        multiplier=Decimal("10"),
        catalog=catalog,
    )

    # 2 per batch, 10 units of a 16-unit batch → 2 * 10/16 = 1.25.
    assert totals[flour.id].quantity == Decimal("1.2500")


async def test_unit_version_is_unaffected():
    flour = _item()
    owner_id = uuid.uuid4()
    version = _version(basis="unit", batch_yield=None, lines=[_line(flour.id, "2")])
    catalog = recipe_service.ActiveRecipeCatalog(
        versions={("inventory_item", owner_id): version},
        items={flour.id: flour},
    )

    totals, _ = await recipe_service.expand_owner(
        MagicMock(),
        kind="inventory_item",
        owner_id=owner_id,
        multiplier=Decimal("10"),
        catalog=catalog,
    )

    assert totals[flour.id].quantity == Decimal("20.0000")


async def test_nested_batch_sub_recipe_composes():
    raw = _item()
    sub = _item(tracking=InventoryTrackingModeEnum.PHANTOM.value)
    parent_id = uuid.uuid4()

    parent = _version(
        basis="batch", batch_yield=Decimal("16"), lines=[_line(sub.id, "2")]
    )
    sub_version = _version(
        basis="batch", batch_yield=Decimal("4"), lines=[_line(raw.id, "3")]
    )
    catalog = recipe_service.ActiveRecipeCatalog(
        versions={
            ("inventory_item", parent_id): parent,
            ("inventory_item", sub.id): sub_version,
        },
        items={sub.id: sub, raw.id: raw},
    )

    totals, _ = await recipe_service.expand_owner(
        MagicMock(),
        kind="inventory_item",
        owner_id=parent_id,
        multiplier=Decimal("10"),
        catalog=catalog,
    )

    # sub demand = 2 * 10/16 = 1.25; raw = 3 * 1.25/4 = 0.9375.
    assert totals[raw.id].quantity == Decimal("0.9375")


# --- request contract ------------------------------------------------------


def _draft(**kwargs):
    from app.schemas.inventory_v2 import RecipeDraftRequest

    return RecipeDraftRequest(
        ingredients=[{"item_id": uuid.uuid4(), "quantity": Decimal("1")}],
        **kwargs,
    )


def test_draft_request_batch_requires_yield():
    _draft(basis="batch", batch_yield=Decimal("16"))  # ok
    with pytest.raises(pydantic.ValidationError):
        _draft(basis="batch")
    with pytest.raises(pydantic.ValidationError):
        _draft(basis="unit", batch_yield=Decimal("16"))
