"""Reset a made item's on-hand cost to its current recipe cost.

A produced/semi-finished good's true cost is its recipe: the FIFO cost of the
ingredients that go into it. When such a good has entered stock at zero or a
stale cost, an admin can restate every on-hand unit to the recipe's current cost
in one action (costing audit — Feature 2 / plan step 2). The revaluation goes
through the standard cost-adjustment path, so it rescales the FIFO layers and
leaves a COST_ADJUSTMENT trail rather than editing an immutable ledger line.
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
from app.services.inventory import inventory_service, recipe_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-recipe-recost"


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
        # Recipe: one Brownie needs 2 Flour + 3 Sugar (ingredient units).
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
        # Recipe rows cascade from the item; levels and settings are explicit.
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


async def test_reset_restates_on_hand_to_current_recipe_cost(env):
    _engine, Session, ids = env

    async with Session() as db:
        user = await db.get(User, ids.user)
        # Ingredients get real costs: Flour @ 2.00, Sugar @ 0.50.
        await _receive(db, ids, ids.flour, user, quantity="100", unit_cost="2.00")
        await _receive(db, ids, ids.sugar, user, quantity="100", unit_cost="0.50")
        # The made good enters stock at zero cost — the leak the reset fixes.
        await _receive(db, ids, ids.made, user, quantity="10", unit_cost="0.00")
        await db.commit()

    async with Session() as db:
        assert await _level_cost(db, ids.made, ids.wh) == Decimal("0")

    # Recipe cost = 2 Flour × 2.00 + 3 Sugar × 0.50 = 5.50 per unit.
    async with Session() as db:
        cost = await recipe_service.recipe_unit_cost(db, item_id=ids.made)
        assert cost == Decimal("5.5")

    async with Session() as db:
        user = await db.get(User, ids.user)
        result = await recipe_service.reset_item_cost_from_recipe(
            db, item_id=ids.made, user=user
        )
        await db.commit()
        assert result["levels_adjusted"] == 1
        assert result["levels_skipped"] == 0
        adj = result["adjustments"][0]
        assert Decimal(str(adj["new_average_cost"])) == Decimal("5.5")
        # 10 units revalued from 0 to 5.50 = +55.00 on the books.
        assert Decimal(str(adj["value_change"])) == Decimal("55")

    async with Session() as db:
        assert await _level_cost(db, ids.made, ids.wh) == Decimal("5.5")


async def test_reset_refuses_when_ingredients_have_no_cost(env):
    """With uncosted ingredients the recipe cost is 0 — there is nothing to reset
    to, and zeroing the stock would be wrong, so the action refuses."""
    from app.core.exceptions import BadRequestError

    _engine, Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _receive(db, ids, ids.made, user, quantity="10", unit_cost="0.00")
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        with pytest.raises(BadRequestError):
            await recipe_service.reset_item_cost_from_recipe(
                db, item_id=ids.made, user=user
            )
