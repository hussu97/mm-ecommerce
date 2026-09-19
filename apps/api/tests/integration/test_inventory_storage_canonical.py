"""A dual-unit item is stocked in its storage unit; a recipe consumes in its
ingredient unit and the ledger converts to storage.

The worked example from the design: Baking Powder, storage=g, ingredient=tsp,
factor 0.25 (0.25 tsp per gram → 1 tsp = 4 g). Buying 100 g leaves 100 g on
hand; a recipe line of 2 tsp takes 8 g off the shelf, and the movement records
both the 2 tsp and the 8 g. Runs against a real Postgres and rolls back — the
whole point is the storage/ingredient conversion, which the mocked unit session
cannot exercise.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import inventory_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-storage-canon"
BUSINESS_DATE = "2026-09-14"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _post(
    db, *, branch, warehouse, user, item, ttype, qty, unit, factor, cost="0"
):
    # Cost is FIFO now: a receipt carries the price it was bought at on the line
    # (an issue's unit_cost is ignored — valuation comes from the layers it draws).
    txn = InventoryTransaction(
        reference=await inventory_service.next_reference(db, ttype),
        type=ttype,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse.id,
        business_date=BUSINESS_DATE,
        creator_id=user.id,
        idempotency_key=f"{ttype}:{uuid.uuid4()}",
        items=[
            InventoryTransactionItem(
                item_id=item.id,
                quantity=Decimal(str(qty)),
                unit=unit,
                conversion_factor=Decimal(str(factor)),
                unit_cost=Decimal(str(cost)),
            )
        ],
    )
    db.add(txn)
    await db.flush()
    await inventory_service.post_transaction(db, transaction=txn, user=user)
    return txn


async def test_buy_in_grams_consume_in_teaspoons(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = User(email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", is_staff=True)
        branch = Branch(
            name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:10]}"
        )
        db.add_all([user, branch])
        await db.flush()
        warehouse = Warehouse(branch_id=branch.id, name="Default", is_default=True)
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            name=f"{MARKER} Baking Powder",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="teaspoon",
            storage_to_ingredient_factor=Decimal("0.25"),  # 1 tsp = 4 g
        )
        db.add_all(
            [
                warehouse,
                item,
                BranchInventorySettings(branch_id=branch.id, inventory_enabled=True),
            ]
        )
        await db.flush()

        # Buy 100 g (entered in the storage unit).
        await _post(
            db,
            branch=branch,
            warehouse=warehouse,
            user=user,
            item=item,
            ttype=InventoryTransactionTypeEnum.PURCHASING.value,
            qty="100",
            unit="storage",
            factor="0.25",
            cost="2",  # 2 per gram
        )
        level = (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.item_id == item.id,
                    InventoryLevel.warehouse_id == warehouse.id,
                )
            )
        ).scalar_one()
        assert level.quantity == Decimal("100.0000")  # grams on hand
        assert level.average_cost == Decimal("2.000000")  # per gram

        # Consume a recipe line of 2 teaspoons.
        consume = await _post(
            db,
            branch=branch,
            warehouse=warehouse,
            user=user,
            item=item,
            ttype=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
            qty="2",
            unit="ingredient",
            factor="0.25",
        )
        await db.refresh(level)
        # 2 tsp ÷ 0.25 = 8 g taken off the shelf.
        assert level.quantity == Decimal("92.0000")
        assert level.average_cost == Decimal("2.000000")  # issue leaves it untouched
        assert level.total_value == Decimal("184.0000")  # 92 g × 2/g

        line = (
            await db.execute(
                select(InventoryTransactionItem).where(
                    InventoryTransactionItem.transaction_id == consume.id
                )
            )
        ).scalar_one()
        # The movement carries BOTH figures: 2 tsp as authored, 8 g off the shelf.
        assert line.quantity == Decimal("2.0000")
        assert line.unit == "ingredient"
        assert line.quantity_in_ingredient_unit == Decimal("2.0000")
        assert line.quantity_in_storage_unit == Decimal("8.0000")
        assert line.signed_quantity == Decimal("-8.000000")

        # Rectifying the conversion factor must NOT move the grams on hand — the
        # whole point of holding stock in the storage unit. Only future
        # ingredient↔gram conversions change.
        item.storage_to_ingredient_factor = Decimal("0.2")  # was 0.25
        await db.flush()
        await db.refresh(level)
        assert level.quantity == Decimal("92.0000")
        assert level.total_value == Decimal("184.0000")

        await db.rollback()
