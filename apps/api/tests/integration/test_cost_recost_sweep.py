"""The background sweep heals made stock that entered the ledger at zero cost.

Made stock can enter at zero (opening balance, a shift-report receipt, a count
overage before the item was ever costed). Nothing in the forward path revalues it
once the recipe has a cost — so a sweep does, idempotently (costing audit G3 /
Feature 4). It only touches zero-cost levels of items with an active recipe, and
only when that recipe now prices to something; an item whose own ingredients are
still uncosted is left until there is a figure to anchor to.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory import InventoryTransactionTypeEnum as TxnType
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import (
    cost_maintenance_service,
    inventory_service,
    recipe_service,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-recost-sweep"


async def _item(db, name: str, kind: str) -> InventoryItem:
    item = InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        name=name,
        kind=kind,
        tracking_mode="stocked",
        storage_unit="pcs",
        ingredient_unit="pcs",
        storage_to_ingredient_factor=Decimal("1"),
    )
    db.add(item)
    await db.flush()
    return item


async def _receive(db, ids, item_id, user, *, quantity: str, unit_cost: str) -> None:
    txn = InventoryTransaction(
        reference=await inventory_service.next_reference(db, TxnType.PURCHASING.value),
        type=TxnType.PURCHASING.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=ids.branch,
        warehouse_id=ids.wh,
        business_date="2026-09-22",
        creator_id=user.id,
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=Decimal(quantity),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=Decimal(unit_cost),
            )
        ],
    )
    db.add(txn)
    await db.flush()
    txn = await inventory_service.load_transaction(db, txn.id)
    await inventory_service.post_transaction(db, transaction=txn, user=user)


@pytest.fixture
async def env():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        wh = Warehouse(branch_id=branch.id, name="Default stock", is_default=True)
        db.add(wh)
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                production_enabled=True,
                sales_consumption_enabled=True,
                allow_negative_stock=True,
                go_live_at=inventory_service.utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        made = await _item(db, "Brownie", "produced_good")
        flour = await _item(db, "Flour", "raw_material")
        sugar = await _item(db, "Sugar", "raw_material")
        for item in (made, flour, sugar):
            db.add(
                InventoryLevel(
                    item_id=item.id, warehouse_id=wh.id, quantity=Decimal("0")
                )
            )
        await db.flush()
        await recipe_service.draft_and_activate(
            db,
            kind="inventory_item",
            owner_id=made.id,
            lines=[
                recipe_service.RecipeLineInput(item_id=flour.id, quantity=Decimal("2")),
                recipe_service.RecipeLineInput(item_id=sugar.id, quantity=Decimal("3")),
            ],
            user_id=user.id,
        )
        await db.commit()
        ids = SimpleNamespace(
            branch=branch.id,
            wh=wh.id,
            user=user.id,
            made=made.id,
            flour=flour.id,
            sugar=sugar.id,
        )
    yield engine, Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        item_ids = [ids.made, ids.flour, ids.sugar]
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == ids.branch
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == ids.branch
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_(item_ids)
            )
        )
        from app.models.inventory_v2 import Recipe

        await db.execute(
            Recipe.__table__.delete().where(Recipe.inventory_item_id.in_(item_ids))
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == ids.branch
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id.in_(item_ids))
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids.branch)
        )
        await db.execute(User.__table__.delete().where(User.id == ids.user))
        await db.execute(Branch.__table__.delete().where(Branch.id == ids.branch))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
    await engine.dispose()


async def _level_cost(db, item_id, warehouse_id) -> Decimal:
    level = (
        await db.execute(
            select(InventoryLevel).where(
                InventoryLevel.item_id == item_id,
                InventoryLevel.warehouse_id == warehouse_id,
            )
        )
    ).scalar_one()
    return Decimal(str(level.average_cost))


async def test_sweep_revalues_zero_cost_made_stock(env):
    _engine, Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _receive(db, ids, ids.flour, user, quantity="100", unit_cost="2.00")
        await _receive(db, ids, ids.sugar, user, quantity="100", unit_cost="0.50")
        # The made good enters at zero cost — the leak the sweep heals.
        await _receive(db, ids, ids.made, user, quantity="10", unit_cost="0.00")
        await db.commit()

    async with Session() as db:
        assert await _level_cost(db, ids.made, ids.wh) == Decimal("0")

    # The sweep is estate-wide, so assert on this item's own record rather than a
    # global count (other rows may exist in a shared test database).
    async with Session() as db:
        fixed = await cost_maintenance_service.sweep_zero_cost_recipe_stock(db)
        await db.commit()
        mine = [f for f in fixed if f["item_id"] == str(ids.made)]
        assert len(mine) == 1
        assert Decimal(str(mine[0]["new_average_cost"])) == Decimal("5.5")

    async with Session() as db:
        assert await _level_cost(db, ids.made, ids.wh) == Decimal("5.5")

    # Idempotent: a second sweep no longer touches this item.
    async with Session() as db:
        again = await cost_maintenance_service.sweep_zero_cost_recipe_stock(db)
        await db.commit()
        assert [f for f in again if f["item_id"] == str(ids.made)] == []


async def test_sweep_skips_when_ingredients_are_uncosted(env):
    """No ingredient receipts → recipe prices to 0 → the sweep leaves the made
    stock alone rather than pinning it at zero."""
    _engine, Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _receive(db, ids, ids.made, user, quantity="10", unit_cost="0.00")
        await db.commit()

    async with Session() as db:
        fixed = await cost_maintenance_service.sweep_zero_cost_recipe_stock(db)
        await db.commit()
        assert [f for f in fixed if f["item_id"] == str(ids.made)] == []
        assert await _level_cost(db, ids.made, ids.wh) == Decimal("0")
