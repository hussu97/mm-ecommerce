"""The maker-checker on recipe creation: a purchased item may not have a recipe."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError
from app.models.inventory import InventoryItem
from app.services.inventory import recipe_service


def _db_returning(owner) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=owner)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.parametrize("kind", ["raw_material", "packaging", "resale_good"])
async def test_recipe_creation_blocked_on_a_purchased_inventory_item(kind):
    # These are bought and counted directly; a recipe would double-count their stock.
    item = InventoryItem(name="Cake Box", kind=kind)
    db = _db_returning(item)
    with pytest.raises(BadRequestError, match="no recipe"):
        await recipe_service.create_draft(
            db, kind="inventory_item", owner_id=uuid.uuid4(), lines=[]
        )


@pytest.mark.parametrize("kind", ["produced_good", "semi_finished"])
async def test_recipe_creation_allowed_on_a_produced_inventory_item(kind, monkeypatch):
    # A produced/semi-finished item IS made from other items, so the owner check must
    # pass it through (the rest of create_draft is exercised against a real DB).
    item = InventoryItem(name="Cheesecake Brownie", kind=kind)
    db = _db_returning(item)

    async def _stop(*a, **k):  # halt right after the owner assertion passes
        raise RuntimeError("owner accepted")

    monkeypatch.setattr(recipe_service, "get_recipe", _stop)
    with pytest.raises(RuntimeError, match="owner accepted"):
        await recipe_service.create_draft(
            db, kind="inventory_item", owner_id=uuid.uuid4(), lines=[]
        )
