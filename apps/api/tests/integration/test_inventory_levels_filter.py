"""The On-Hand levels endpoint filters in SQL: search, category, below-minimum.

The filters run before the `limit`, so a search or category view is correct
across the whole estate rather than only whichever rows fit the first 500.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.inventory import list_levels
from app.models import Branch, User
from app.models.inventory import (
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    Warehouse,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-levels-filter"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _item(*, name, sku, category_id, minimum):
    return InventoryItem(
        name=name,
        sku=sku,
        category_id=category_id,
        kind="raw_material",
        ingredient_unit="g",
        minimum_level=Decimal(str(minimum)),
        par_level=Decimal("100"),
    )


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        wh = Warehouse(branch_id=branch.id, name="Store", is_default=True)
        cat = InventoryCategory(
            name=f"{MARKER} Bakery", reference=f"{MARKER}-cat-{uuid.uuid4().hex[:8]}"
        )
        admin = User(
            email=f"lvl-{uuid.uuid4().hex[:10]}@example.com",
            is_staff=True,
            is_admin=True,
        )
        db.add_all([wh, cat, admin])
        await db.flush()

        # Flour: in the category, plenty in stock. Sugar: no category, below min.
        flour = _item(
            name=f"{MARKER} Flour",
            sku=f"FLR-{uuid.uuid4().hex[:6]}",
            category_id=cat.id,
            minimum=10,
        )
        sugar = _item(
            name=f"{MARKER} Sugar",
            sku=f"SGR-{uuid.uuid4().hex[:6]}",
            category_id=None,
            minimum=50,
        )
        db.add_all([flour, sugar])
        await db.flush()
        db.add_all(
            [
                InventoryLevel(
                    item_id=flour.id,
                    warehouse_id=wh.id,
                    quantity=Decimal("80"),
                    average_cost=Decimal("0.5"),
                ),
                InventoryLevel(
                    item_id=sugar.id,
                    warehouse_id=wh.id,
                    quantity=Decimal("5"),
                    average_cost=Decimal("0.3"),
                ),
            ]
        )
        await db.commit()
        ids = dict(
            branch_id=branch.id,
            warehouse_id=wh.id,
            category_id=cat.id,
            admin_id=admin.id,
            flour_id=flour.id,
            sugar_id=sugar.id,
        )
    yield ids

    async with Session() as db:
        await db.execute(
            InventoryLevel.__table__.delete().where(
                InventoryLevel.warehouse_id == ids["warehouse_id"]
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_([ids["flour_id"], ids["sugar_id"]])
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.id == ids["warehouse_id"])
        )
        await db.execute(
            InventoryCategory.__table__.delete().where(
                InventoryCategory.id == ids["category_id"]
            )
        )
        await db.execute(User.__table__.delete().where(User.id == ids["admin_id"]))
        await db.execute(Branch.__table__.delete().where(Branch.id == ids["branch_id"]))
        await db.commit()


async def _names(engine, admin_id, **kwargs):
    # `limit` carries a FastAPI `Query(...)` default, which is only resolved to an
    # int when the route is called through the app — pass it explicitly here.
    kwargs.setdefault("limit", 500)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        admin = await db.get(User, admin_id)
        rows = await list_levels(db=db, user=admin, **kwargs)
    return {r.item_name for r in rows}


async def test_search_matches_name_across_the_estate(engine, seeded):
    names = await _names(
        engine, seeded["admin_id"], search="Flour", branch_id=seeded["branch_id"]
    )
    assert names == {f"{MARKER} Flour"}


async def test_category_filter_scopes_to_one_category(engine, seeded):
    names = await _names(
        engine,
        seeded["admin_id"],
        category_id=seeded["category_id"],
        branch_id=seeded["branch_id"],
    )
    assert names == {f"{MARKER} Flour"}


async def test_below_minimum_only_is_applied_in_sql(engine, seeded):
    names = await _names(
        engine,
        seeded["admin_id"],
        below_minimum_only=True,
        branch_id=seeded["branch_id"],
    )
    assert names == {f"{MARKER} Sugar"}  # 5 < 50, while flour 80 > 10


async def test_no_filter_returns_both(engine, seeded):
    names = await _names(engine, seeded["admin_id"], branch_id=seeded["branch_id"])
    assert names == {f"{MARKER} Flour", f"{MARKER} Sugar"}
