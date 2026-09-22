"""A product's cost is its live recipe cost now (Product.cost was dropped).

recipe_service.product_recipe_unit_cost expands a product's active recipe to its
leaf ingredients and prices them at estate-wide FIFO cost — the figure the CSV
export now carries in place of the stale, CSV-imported column.
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
from app.models.product import Product
from app.models.user import User
from app.services.inventory import inventory_service, recipe_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-product-cost"


async def _ingredient(db, name: str) -> InventoryItem:
    item = InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        name=name,
        kind="raw_material",
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
        flour = await _ingredient(db, "Flour")
        sugar = await _ingredient(db, "Sugar")
        for item in (flour, sugar):
            db.add(
                InventoryLevel(
                    item_id=item.id, warehouse_id=wh.id, quantity=Decimal("0")
                )
            )
        product = Product(
            name=f"{MARKER} Cake", slug=f"{MARKER}-{uuid.uuid4().hex[:10]}"
        )
        db.add(product)
        await db.flush()
        # A product owns its recipe directly (owner_kind='product'): 4 Flour + 2 Sugar.
        await recipe_service.draft_and_activate(
            db,
            kind="product",
            owner_id=product.id,
            lines=[
                recipe_service.RecipeLineInput(item_id=flour.id, quantity=Decimal("4")),
                recipe_service.RecipeLineInput(item_id=sugar.id, quantity=Decimal("2")),
            ],
            user_id=user.id,
        )
        await db.commit()
        ids = SimpleNamespace(
            branch=branch.id,
            wh=wh.id,
            user=user.id,
            product=product.id,
            flour=flour.id,
            sugar=sugar.id,
        )
    yield engine, Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        item_ids = [ids.flour, ids.sugar]
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
            Recipe.__table__.delete().where(Recipe.product_id == ids.product)
        )
        await db.execute(Product.__table__.delete().where(Product.id == ids.product))
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


async def test_product_recipe_unit_cost_from_ingredient_fifo(env):
    _engine, Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _receive(db, ids, ids.flour, user, quantity="100", unit_cost="1.50")
        await _receive(db, ids, ids.sugar, user, quantity="100", unit_cost="0.25")
        await db.commit()

    # 4 Flour × 1.50 + 2 Sugar × 0.25 = 6.00 + 0.50 = 6.50 per product unit.
    async with Session() as db:
        cost = await recipe_service.product_recipe_unit_cost(db, product_id=ids.product)
        assert cost == Decimal("6.5")


async def test_product_with_no_recipe_has_no_cost(env):
    """A product with no active recipe returns None — an empty export cost cell,
    not a spurious zero."""
    _engine, Session, ids = env
    async with Session() as db:
        bare = Product(name=f"{MARKER} Bare", slug=f"{MARKER}-{uuid.uuid4().hex[:10]}")
        db.add(bare)
        await db.flush()
        bare_id = bare.id
        assert (
            await recipe_service.product_recipe_unit_cost(db, product_id=bare_id)
            is None
        )
        await db.rollback()
