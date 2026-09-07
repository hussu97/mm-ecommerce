"""A retried manual transaction posts stock exactly once (F-INV-11).

Calls the endpoint handler directly against a real Postgres: a second POST with
the same idempotency key returns the first transaction and never moves stock a
second time.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.inventory import create_transaction
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.schemas.inventory import InventoryTransactionCreate
from app.services.inventory import report_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-manual-txn"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        warehouse = Warehouse(
            branch_id=branch.id, name="Default stock", is_default=True
        )
        db.add(warehouse)
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                sales_consumption_enabled=True,
                go_live_at=report_service.utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_admin=True,
        )
        db.add(user)
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Flour",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="kg",
            ingredient_unit="kg",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("2"),
        )
        db.add(item)
        await db.commit()
        ids = (branch.id, warehouse.id, user.id, item.id)
    yield ids
    branch_id, _, user_id, item_id = ids
    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == branch_id
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(InventoryLevel.item_id == item_id)
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == branch_id
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_a_repeated_idempotency_key_posts_stock_once(engine, env):
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)

    def payload():
        return InventoryTransactionCreate(
            type="quantity_adjustment",
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            idempotency_key=f"{MARKER}-key-1",
            items=[{"item_id": item_id, "quantity": "10", "unit": "storage"}],
        )

    async with Session() as db:
        user = await db.get(User, user_id)
        first = await create_transaction(payload(), post=True, db=db, user=user)
        await db.commit()

    async with Session() as db:
        user = await db.get(User, user_id)
        second = await create_transaction(payload(), post=True, db=db, user=user)
        await db.commit()

    assert first.id == second.id, "the retry must return the first transaction"

    async with Session() as db:
        rows = (
            await db.execute(
                select(InventoryTransaction.id).where(
                    InventoryTransaction.branch_id == branch_id
                )
            )
        ).all()
        assert len(rows) == 1, "no second transaction row"
        level = (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.item_id == item_id,
                    InventoryLevel.warehouse_id == warehouse_id,
                )
            )
        ).scalar_one()
        assert level.quantity == Decimal("10"), "stock moved exactly once"
