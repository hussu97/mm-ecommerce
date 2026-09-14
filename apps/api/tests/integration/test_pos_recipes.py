"""The POS Recipes tab lists only made items with a live (active) recipe.

`recipe_catalog_service.list_active_inventory_recipes` is what the register's
read-only Recipes tab reads. It joins made inventory items to their recipe's
*active* version and to the user who activated it, and attaches the ingredient
lines. These clauses — the active-only join, the made-kind + is_active scope,
the activated_by name resolution, the line ordering — only mean anything
against a real Postgres, so this runs there like the console-list test beside
it (the unit suite mocks the session).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.inventory import InventoryItem
from app.models.inventory_v2 import Recipe, RecipeLine, RecipeVersion
from app.models.user import User
from app.services.inventory import recipe_catalog_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-pos-recipes"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _item(name: str, *, kind: str, is_active: bool = True) -> InventoryItem:
    return InventoryItem(
        name=name,
        sku=f"{MARKER[:6].upper()}-{uuid.uuid4().hex[:8]}",
        kind=kind,
        ingredient_unit="g",
        minimum_level=Decimal("0"),
        par_level=Decimal("0"),
        is_active=is_active,
    )


async def _recipe(db, *, owner_id, status, ingredient_id, activated_by=None):
    """An inventory-item recipe with one version and one line.

    Lines are added while the version is a draft (a trigger freezes them once
    published), then the version is promoted — stamping activated_by/at when it
    goes active, exactly as the activate endpoint does.
    """
    recipe = Recipe(owner_kind="inventory_item", inventory_item_id=owner_id)
    db.add(recipe)
    await db.flush()
    version = RecipeVersion(recipe_id=recipe.id, version_number=1, status="draft")
    db.add(version)
    await db.flush()
    db.add(
        RecipeLine(
            recipe_version_id=version.id,
            item_id=ingredient_id,
            quantity=Decimal("2"),
            ingredient_unit="g",
            yield_percentage=Decimal("0.5"),
        )
    )
    await db.flush()
    if status != "draft":
        version.status = status
        if status == "active":
            version.activated_at = datetime.now(timezone.utc)
            version.activated_by = activated_by
        await db.flush()


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        activator = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            display_name=f"{MARKER} Baker",
            is_staff=True,
        )
        ingredient = _item(f"{MARKER} Flour", kind="raw_material")
        live = _item(f"{MARKER} Cake Base", kind="produced_good")  # active → shown
        draft_only = _item(f"{MARKER} Sauce", kind="semi_finished")  # draft → hidden
        inactive = _item(f"{MARKER} Old Base", kind="produced_good", is_active=False)
        db.add_all([activator, ingredient, live, draft_only, inactive])
        await db.flush()

        await _recipe(
            db,
            owner_id=live.id,
            status="active",
            ingredient_id=ingredient.id,
            activated_by=activator.id,
        )
        await _recipe(
            db,
            owner_id=draft_only.id,
            status="draft",
            ingredient_id=ingredient.id,
        )
        await _recipe(
            db,
            owner_id=inactive.id,
            status="active",
            ingredient_id=ingredient.id,
            activated_by=activator.id,
        )
        await db.commit()
        ids = {
            "activator": activator.id,
            "ingredient": ingredient.id,
            "live": live.id,
            "draft_only": draft_only.id,
            "inactive": inactive.id,
        }
    yield ids

    async with Session() as db:
        owned = Recipe.inventory_item_id.in_(
            [ids["live"], ids["draft_only"], ids["inactive"]]
        )
        # Active/retired recipes are immutable by design (three triggers); a
        # test can only remove its own fixtures with the guards briefly off
        # (mm_user is superuser on the throwaway DB).
        for trigger, table in (
            ("recipe_line_immutable", "recipe_lines"),
            ("recipe_version_immutable", "recipe_versions"),
            ("recipe_owner_immutable", "recipes"),
        ):
            await db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        await db.execute(Recipe.__table__.delete().where(owned))
        await db.execute(
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_(
                    [ids["ingredient"], ids["live"], ids["draft_only"], ids["inactive"]]
                )
            )
        )
        await db.execute(User.__table__.delete().where(User.id == ids["activator"]))
        for trigger, table in (
            ("recipe_owner_immutable", "recipes"),
            ("recipe_version_immutable", "recipe_versions"),
            ("recipe_line_immutable", "recipe_lines"),
        ):
            await db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        await db.commit()


async def _cards(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        cards = await recipe_catalog_service.list_active_inventory_recipes(db)
    return [c for c in cards if c["name"].startswith(MARKER)]


async def test_only_active_made_items_appear(engine, seeded):
    cards = await _cards(engine)
    names = {c["name"] for c in cards}
    # The live produced_good is shown; the draft-only and the inactive are not.
    assert names == {f"{MARKER} Cake Base"}


async def test_card_carries_version_activator_and_lines(engine, seeded):
    (card,) = await _cards(engine)
    assert card["item_id"] == seeded["live"]
    assert card["version_number"] == 1
    assert card["activated_by_name"] == f"{MARKER} Baker"
    assert card["activated_at"] is not None
    assert card["line_count"] == 1
    (line,) = card["ingredients"]
    assert line["name"] == f"{MARKER} Flour"
    assert line["quantity"] == Decimal("2")
    assert line["unit"] == "g"
    assert line["yield_percentage"] == Decimal("0.5")
