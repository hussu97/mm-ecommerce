"""Re-cost a production batch after the fact, through the ledger.

A batch is costed at what its recorded inputs cost; one baked from a recipe that
left an ingredient out carries too little. `restate_production_cost` books a
`production_restatement` against the batch, and the costing engine re-prices the
batch and everything drawn from it — here, a sale — from the bake onwards.
Reversing the restatement puts the recorded cost back.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryItem,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory import InventoryTransactionTypeEnum as TxnType
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import inventory_service, ledger_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-restate"
D = Decimal


async def _post(db, ids, user, type_, lines, *, group=None) -> InventoryTransaction:
    txn = InventoryTransaction(
        reference=await inventory_service.next_reference(db, type_),
        type=type_,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=ids.branch,
        warehouse_id=ids.wh,
        business_date="2026-09-10",
        creator_id=user.id,
        correction_group_id=group,
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=D(quantity),
                unit="storage",
                conversion_factor=D("1"),
                unit_cost=D(unit_cost),
            )
            for item_id, quantity, unit_cost in lines
        ],
    )
    db.add(txn)
    await db.flush()
    return await inventory_service.post_transaction(db, transaction=txn, user=user)


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
                allow_negative_stock=True,
                go_live_at=inventory_service.utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        items = {}
        for name, kind in (("Flour", "raw_material"), ("Cake", "produced_good")):
            item = InventoryItem(
                sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
                name=name,
                kind=kind,
                tracking_mode="stocked",
                storage_unit="pcs",
                ingredient_unit="pcs",
                storage_to_ingredient_factor=D("1"),
            )
            db.add(item)
            items[name] = item
        await db.commit()
        ids = SimpleNamespace(
            branch=branch.id,
            wh=wh.id,
            user=user.id,
            flour=items["Flour"].id,
            cake=items["Cake"].id,
        )
    yield Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        txn_ids = select(InventoryTransaction.id).where(
            InventoryTransaction.branch_id == ids.branch
        )
        line_ids = select(InventoryTransactionItem.id).where(
            InventoryTransactionItem.transaction_id.in_(txn_ids)
        )
        item_ids = [ids.flour, ids.cake]
        for stmt in (
            InventoryCostLayerConsumption.__table__.delete().where(
                InventoryCostLayerConsumption.consuming_line_id.in_(line_ids)
            ),
            InventoryCostLayer.__table__.delete().where(
                InventoryCostLayer.branch_id == ids.branch
            ),
            InventoryLineCost.__table__.delete().where(
                InventoryLineCost.branch_id == ids.branch
            ),
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(txn_ids)
            ),
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == ids.branch
            ),
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_(item_ids)
            ),
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == ids.branch
            ),
            InventoryItem.__table__.delete().where(InventoryItem.id.in_(item_ids)),
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids.branch),
            User.__table__.delete().where(User.id == ids.user),
            Branch.__table__.delete().where(Branch.id == ids.branch),
        ):
            await db.execute(stmt)
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
    await engine.dispose()


async def _line_cost(db, line_id) -> InventoryLineCost:
    return await db.get(InventoryLineCost, line_id, populate_existing=True)


async def _avg(db, item_id, warehouse_id) -> Decimal:
    level = (
        await db.execute(
            select(InventoryLevel)
            .where(
                InventoryLevel.item_id == item_id,
                InventoryLevel.warehouse_id == warehouse_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return D(str(level.average_cost))


async def test_a_restated_batch_reprices_its_sales_and_reverses_cleanly(env):
    Session, ids = env
    group = uuid.uuid4()
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _post(db, ids, user, TxnType.PURCHASING.value, [(ids.flour, "100", "2")])
        # 5 cakes from 10 flour: recorded at 20 / 5 = 4 a cake.
        await _post(
            db,
            ids,
            user,
            TxnType.CONSUMPTION_FROM_PRODUCTION.value,
            [(ids.flour, "10", "0")],
            group=group,
        )
        batch = await _post(
            db, ids, user, TxnType.PRODUCTION.value, [(ids.cake, "5", "0")], group=group
        )
        sale = await _post(
            db, ids, user, TxnType.CONSUMPTION_FROM_ORDERS.value, [(ids.cake, "2", "0")]
        )
        await db.commit()
        sale_line = sale.items[0].id

    async with Session() as db:
        assert (await _line_cost(db, sale_line)).total_cost == D("8")

    async with Session() as db:
        user = await db.get(User, ids.user)
        restatement = await inventory_service.restate_production_cost(
            db, production_transaction_id=batch.id, unit_cost=D("7"), user=user
        )
        await db.commit()
        assert restatement.type == TxnType.PRODUCTION_RESTATEMENT.value
        assert restatement.correction_group_id == group
        assert restatement.items[0].signed_quantity == 0

    async with Session() as db:
        assert (await _line_cost(db, sale_line)).total_cost == D("14")
        assert await _avg(db, ids.cake, ids.wh) == D("7")
        # The restatement line carries the revaluation: 5 × (7 − 4).
        line = await _line_cost(db, restatement.items[0].id)
        assert line.total_cost == D("15")
        # No stock moved.
        level = (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.item_id == ids.cake,
                    InventoryLevel.warehouse_id == ids.wh,
                )
            )
        ).scalar_one()
        assert D(str(level.quantity)) == D("3")

    async with Session() as db:
        user = await db.get(User, ids.user)
        await ledger_service.reverse_transaction(
            db, transaction_id=restatement.id, user=user, reason="test"
        )
        await db.commit()

    async with Session() as db:
        assert (await _line_cost(db, sale_line)).total_cost == D("8")
        assert await _avg(db, ids.cake, ids.wh) == D("4")


async def test_only_a_posted_batch_can_be_restated(env):
    Session, ids = env
    async with Session() as db:
        user = await db.get(User, ids.user)
        receipt = await _post(
            db, ids, user, TxnType.PURCHASING.value, [(ids.flour, "10", "2")]
        )
        with pytest.raises(BadRequestError):
            await inventory_service.restate_production_cost(
                db, production_transaction_id=receipt.id, unit_cost=D("1"), user=user
            )
