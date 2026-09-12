"""The Recipes console list endpoint filters, sorts and paginates in SQL.

`recipe_catalog_service.list_recipe_owners` joins owners to their recipes and
derives has-recipe / status / line counts. These run against a real Postgres
because the unit suite mocks the session — a mock cannot tell a correct join
from a broken one, and every clause here (the LEFT JOINs, the version summary,
the made-kind scope, the count-then-page) is only exercised by the database.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.inventory import InventoryItem
from app.models.inventory_v2 import Recipe, RecipeLine, RecipeVersion
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.services.inventory import recipe_catalog_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-recipe-owners"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _product(name: str, *, is_active: bool) -> Product:
    return Product(
        name=name,
        slug=f"{MARKER}-{uuid.uuid4().hex[:12]}",
        is_active=is_active,
    )


def _item(name: str, *, kind: str) -> InventoryItem:
    return InventoryItem(
        name=name,
        sku=f"{MARKER[:6].upper()}-{uuid.uuid4().hex[:8]}",
        kind=kind,
        ingredient_unit="g",
        minimum_level=Decimal("0"),
        par_level=Decimal("0"),
    )


async def _recipe(db, *, owner_kind, owner_id, status, ingredient_id):
    """A recipe with one version and a single line.

    Lines are inserted while the version is still a draft — a DB trigger makes
    them immutable once the version is published — then the version is promoted
    to the requested status.
    """
    recipe = Recipe(owner_kind=owner_kind, **{f"{owner_kind}_id": owner_id})
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
            yield_percentage=Decimal("1"),
        )
    )
    await db.flush()
    if status != "draft":
        version.status = status
        await db.flush()


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        ingredient = _item(f"{MARKER} Flour", kind="raw_material")
        made = _item(f"{MARKER} Cake Base", kind="produced_good")
        apple = _product(f"{MARKER} Apple", is_active=True)  # active, has recipe
        mango = _product(f"{MARKER} Mango", is_active=True)  # active, no recipe
        zebra = _product(f"{MARKER} Zebra", is_active=False)  # inactive, no recipe
        modifier = Modifier(
            name=f"{MARKER} Toppings",
            reference=f"{MARKER}-mod-{uuid.uuid4().hex[:8]}",
        )
        db.add_all([ingredient, made, apple, mango, zebra, modifier])
        await db.flush()

        option = ModifierOption(
            modifier_id=modifier.id,
            name=f"{MARKER} Sprinkles",
            sku=f"{MARKER[:6].upper()}-OPT-{uuid.uuid4().hex[:8]}",
        )
        # The modifier is carried by two products, so the option's row should
        # list both — in Product.name order.
        db.add_all(
            [
                option,
                ProductModifier(product_id=apple.id, modifier_id=modifier.id),
                ProductModifier(product_id=mango.id, modifier_id=modifier.id),
            ]
        )
        await db.flush()

        await _recipe(
            db,
            owner_kind="product",
            owner_id=apple.id,
            status="active",
            ingredient_id=ingredient.id,
        )
        await _recipe(
            db,
            owner_kind="inventory_item",
            owner_id=made.id,
            status="draft",
            ingredient_id=ingredient.id,
        )
        await db.commit()
        ids = {
            "ingredient": ingredient.id,
            "made": made.id,
            "apple": apple.id,
            "mango": mango.id,
            "zebra": zebra.id,
            "modifier": modifier.id,
            "option": option.id,
        }
    yield ids

    async with Session() as db:
        owned = Recipe.product_id.in_(
            [ids["apple"], ids["mango"], ids["zebra"]]
        ) | Recipe.inventory_item_id.in_([ids["made"], ids["ingredient"]])
        # Active/retired recipes are immutable by design — three DB triggers
        # forbid deleting a recipe, its published versions, or their lines. That
        # invariant protects production; it also means a test's own fixtures can
        # only be removed with the guards briefly disabled (mm_user is superuser
        # on the throwaway DB).
        for trigger, table in (
            ("recipe_line_immutable", "recipe_lines"),
            ("recipe_version_immutable", "recipe_versions"),
            ("recipe_owner_immutable", "recipes"),
        ):
            await db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        await db.execute(Recipe.__table__.delete().where(owned))
        await db.execute(
            Product.__table__.delete().where(
                Product.id.in_([ids["apple"], ids["mango"], ids["zebra"]])
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_([ids["ingredient"], ids["made"]])
            )
        )
        # Cascades the option and both product_modifier links.
        await db.execute(
            Modifier.__table__.delete().where(Modifier.id == ids["modifier"])
        )
        for trigger, table in (
            ("recipe_owner_immutable", "recipes"),
            ("recipe_version_immutable", "recipe_versions"),
            ("recipe_line_immutable", "recipe_lines"),
        ):
            await db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        await db.commit()


async def _list(engine, owner_kind, **kwargs):
    kwargs.setdefault("search", MARKER)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        return await recipe_catalog_service.list_recipe_owners(
            db, owner_kind=owner_kind, **kwargs
        )


async def test_products_all(engine, seeded):
    items, total = await _list(engine, "product")
    assert total == 3
    assert {row["name"] for row in items} == {
        f"{MARKER} Apple",
        f"{MARKER} Mango",
        f"{MARKER} Zebra",
    }


async def test_recipe_with_filter(engine, seeded):
    items, total = await _list(engine, "product", recipe="with")
    assert total == 1
    assert items[0]["name"] == f"{MARKER} Apple"
    assert items[0]["has_recipe"] is True
    assert items[0]["recipe_status"] == "active"
    assert items[0]["active_version_number"] == 1
    assert items[0]["line_count"] == 1
    # The read-only summary carries the active version's lines.
    assert items[0]["ingredients"] == [
        {"name": f"{MARKER} Flour", "quantity": Decimal("2"), "unit": "g"}
    ]


async def test_recipe_without_filter(engine, seeded):
    items, _ = await _list(engine, "product", recipe="without")
    assert {row["name"] for row in items} == {f"{MARKER} Mango", f"{MARKER} Zebra"}


async def test_active_filter(engine, seeded):
    active, _ = await _list(engine, "product", active="active")
    assert {row["name"] for row in active} == {f"{MARKER} Apple", f"{MARKER} Mango"}
    inactive, _ = await _list(engine, "product", active="inactive")
    assert {row["name"] for row in inactive} == {f"{MARKER} Zebra"}


async def test_search_scopes(engine, seeded):
    items, total = await _list(engine, "product", search=f"{MARKER} Mango")
    assert total == 1
    assert items[0]["name"] == f"{MARKER} Mango"


async def test_sort_name_direction(engine, seeded):
    asc, _ = await _list(engine, "product", sort="name", sort_dir="asc")
    desc, _ = await _list(engine, "product", sort="name", sort_dir="desc")
    assert [row["name"] for row in asc] == [row["name"] for row in reversed(desc)]
    assert asc[0]["name"] == f"{MARKER} Apple"


async def test_pagination(engine, seeded):
    page1, total = await _list(engine, "product", page=1, per_page=2)
    page2, _ = await _list(engine, "product", page=2, per_page=2)
    assert total == 3
    assert len(page1) == 2
    assert len(page2) == 1


async def test_inventory_lists_only_made_items(engine, seeded):
    items, total = await _list(engine, "inventory_item")
    # The raw-material ingredient is excluded; only the made item can own a recipe.
    assert {row["name"] for row in items} == {f"{MARKER} Cake Base"}
    assert total == 1
    row = items[0]
    assert row["kind"] == "produced_good"
    assert row["recipe_status"] == "draft"
    assert row["draft_version_number"] == 1
    assert row["line_count"] == 1


async def test_modifier_option_lists_modifier_and_product_names(engine, seeded):
    items, total = await _list(engine, "modifier_option")
    assert total == 1
    row = items[0]
    assert row["name"] == f"{MARKER} Sprinkles"
    # secondary is the parent modifier's name.
    assert row["secondary"] == f"{MARKER} Toppings"
    # product_names lists every product carrying the modifier, in name order.
    assert row["product_names"] == [f"{MARKER} Apple", f"{MARKER} Mango"]
