"""Product and option recipe cost against price, for the console's catalogue.

Pins the arithmetic a sale follows (the product's own recipe + the chosen
option's, against base price + option price), the no-recipe cases, and that a
page of products costs a fixed number of queries however long it is.

Everything runs in one transaction that is rolled back, so nothing is left in
the test database.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import InventoryItem, Warehouse
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.product import Product
from app.models.user import User
from app.services.catalog import product_cost_service, product_service
from app.services.inventory import recipe_service
from app.services.inventory.recipe_service import RecipeLineInput
from tests.integration._stock import seed_stock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

D = Decimal
MARKER = "pytest-pcost"


def _tag() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _world(db, *, extra_plain: int = 0):
    branch = Branch(name=f"{MARKER} branch", reference=f"{MARKER}-{_tag()}")
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Kitchen", is_default=True)
    user = User(email=f"{MARKER}-{_tag()}@example.com", hashed_password="x")
    items = {}
    for name, kind in (("Cake", "produced_good"), ("Box", "packaging")):
        items[name] = InventoryItem(
            sku=f"{MARKER}-{_tag()}",
            name=name,
            kind=kind,
            tracking_mode="stocked",
            storage_unit="pcs",
            ingredient_unit="pcs",
            storage_to_ingredient_factor=D("1"),
        )
    db.add_all([warehouse, user, *items.values()])
    await db.flush()
    # Cake costs 4.00, a box 1.00.
    for item, cost in ((items["Cake"], "4"), (items["Box"], "1")):
        await seed_stock(
            db,
            branch_id=branch.id,
            warehouse_id=warehouse.id,
            item_id=item.id,
            quantity="100",
            unit_cost=cost,
        )

    def product(name, price, **kw):
        return Product(
            name=name,
            slug=f"{MARKER}-{_tag()}",
            base_price=D(price),
            sales_channels=["web"],
            **kw,
        )

    plain = product("Brownie", "10")  # cake + box = 5.00 → 50%
    boxed = product("Box of cookies", "5")  # own recipe: the box (1.00)
    bare = product("Tub", "0", consumes_stock=False)  # options carry it all
    orphan = product("No recipe", "8")
    filling = Modifier(reference=f"{MARKER}-{_tag()}", name="Filling")
    extras = [product(f"Extra {n}", "10") for n in range(extra_plain)]
    db.add_all([plain, boxed, bare, orphan, filling, *extras])
    await db.flush()
    lotus = ModifierOption(
        modifier_id=filling.id, name="Lotus", sku=f"{MARKER}-{_tag()}", price=D("15")
    )
    plainopt = ModifierOption(
        modifier_id=filling.id, name="Plain", sku=f"{MARKER}-{_tag()}", price=D("0")
    )
    retired = ModifierOption(
        modifier_id=filling.id,
        name="Retired",
        sku=f"{MARKER}-{_tag()}",
        price=D("3"),
        is_active=False,
    )
    db.add_all([lotus, plainopt, retired])
    db.add_all(
        [
            ProductModifier(product_id=boxed.id, modifier_id=filling.id),
            ProductModifier(product_id=bare.id, modifier_id=filling.id),
        ]
    )
    await db.flush()
    recipes = [
        ("product", plain.id, [(items["Cake"], "1"), (items["Box"], "1")]),
        ("product", boxed.id, [(items["Box"], "1")]),
        ("modifier_option", lotus.id, [(items["Cake"], "2")]),  # 8.00
        *[("product", p.id, [(items["Cake"], "1")]) for p in extras],
    ]
    for kind, owner_id, lines in recipes:
        await recipe_service.draft_and_activate(
            db,
            kind=kind,
            owner_id=owner_id,
            lines=[RecipeLineInput(item_id=i.id, quantity=D(q)) for i, q in lines],
            user_id=user.id,
        )
    await db.flush()
    return {
        "plain": plain,
        "boxed": boxed,
        "bare": bare,
        "orphan": orphan,
        "extras": extras,
    }


async def test_costs_follow_what_a_sale_draws(db):
    w = await _world(db)
    ids = [w[k].id for k in ("plain", "boxed", "bare", "orphan")]
    rows = {r.product_id: r for r in await product_cost_service.product_costs(db, ids)}
    assert [
        r.product_id for r in await product_cost_service.product_costs(db, ids)
    ] == ids

    plain = rows[w["plain"].id]
    assert (plain.price, plain.cost, plain.cost_pct) == (D("10"), D("5"), D("50"))
    assert plain.options == [] and not plain.missing_recipe

    # Box of cookies: base 5 + its box 1.00; Lotus adds 15 and 8.00 of cake.
    boxed = rows[w["boxed"].id]
    assert (boxed.cost, boxed.cost_pct) == (D("1"), D("20"))
    by_name = {o.name: o for o in boxed.options}
    assert set(by_name) == {"Lotus", "Plain"}  # the inactive option is left out
    assert (by_name["Lotus"].price, by_name["Lotus"].cost) == (D("20"), D("9"))
    assert by_name["Lotus"].cost_pct == D("45")
    # An option with no recipe of its own is unknown, not free.
    assert by_name["Plain"].cost is None and by_name["Plain"].cost_pct is None

    # Consumes no stock: its own part is zero, the option carries the cost.
    bare = rows[w["bare"].id]
    assert bare.cost == D("0") and not bare.missing_recipe
    lotus = next(o for o in bare.options if o.name == "Lotus")
    assert (lotus.price, lotus.cost, lotus.cost_pct) == (D("15"), D("8"), D("53.33"))

    orphan = rows[w["orphan"].id]
    assert orphan.cost is None and orphan.cost_pct is None and orphan.missing_recipe


async def test_a_page_costs_a_fixed_number_of_queries(db):
    w = await _world(db, extra_plain=12)
    few = [w["plain"].id, w["boxed"].id]
    many = few + [w["bare"].id, w["orphan"].id] + [p.id for p in w["extras"]]

    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    sync_engine = db.bind.sync_engine if hasattr(db.bind, "sync_engine") else db.bind
    event.listen(sync_engine, "before_cursor_execute", count)
    try:
        statements.clear()
        await product_cost_service.product_costs(db, few)
        small = len(statements)
        statements.clear()
        rows = await product_cost_service.product_costs(db, many)
        large = len(statements)
    finally:
        event.remove(sync_engine, "before_cursor_execute", count)
    assert len(rows) == len(many)
    assert large == small, f"{small} queries for 2 products, {large} for {len(many)}"
    # Products (with their default eager relations) + modifiers + options,
    # warehouses, the recipe catalogue and one ingredient-cost query: a
    # constant, measured at 13.
    assert 0 < small <= 15, statements


async def _names_for(db, sort, ids):
    """The console list's order for *sort*, narrowed to the test's products."""
    items, _ = await product_service.get_all(
        db,
        search=None,
        sort=sort,
        per_page=2000,
        include_inactive=True,
        channel="all",
        staff=True,
    )
    return [p.name for p in items if p.id in ids]


async def test_the_list_sorts_by_cost_and_cost_share(db):
    w = await _world(db)
    ids = {w[k].id for k in ("plain", "boxed", "bare", "orphan")}
    # Headline figures — an options product sorts by its cheapest option:
    #   Brownie 5.00 / 50%; Box of cookies' cheapest is Plain (no recipe);
    #   Tub's cheapest is Lotus 8.00 / 53.33%; No recipe: unknown.
    assert await _names_for(db, "cost_asc", ids) == [
        "Brownie",
        "Tub",
        "Box of cookies",
        "No recipe",
    ]
    assert await _names_for(db, "cost_desc", ids) == [
        "Tub",
        "Brownie",
        "Box of cookies",  # unknowns stay last, by name
        "No recipe",
    ]
    assert await _names_for(db, "cost_pct_desc", ids) == [
        "Tub",
        "Brownie",
        "Box of cookies",
        "No recipe",
    ]


async def test_cost_sort_pages_do_not_overlap(db):
    w = await _world(db, extra_plain=6)
    mine = {w["plain"].id, *(p.id for p in w["extras"])}
    seen: list = []
    for page in (1, 2, 3, 4, 5, 6, 7, 8):
        items, _ = await product_service.get_all(
            db,
            search="Extra",
            sort="cost_pct_asc",
            page=page,
            per_page=2,
            include_inactive=True,
            channel="all",
            staff=True,
        )
        seen.extend(p.id for p in items if p.id in mine)
    assert len(seen) == len(set(seen)) == len(w["extras"])
