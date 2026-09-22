"""A transfer must carry the cost the source FIFO layers actually released.

Costing audit G1: the send leg consumes the source's oldest cost layers (real
historical cost), but the receive leg used to value the destination at the
source's *moving-average* snapshot instead. When the source's oldest layers cost
differently from its blended average, the two legs disagreed on the value of the
same goods — network inventory value drifted on every transfer, even at
identical send/receive quantities (the AED 12.27-sent / 22.78-received symptom on
TO-003741). The receive leg now values the destination at the send's actual
consumed FIFO cost, so the two legs balance by construction.
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
from app.models.operations import TransferOrder
from app.models.user import User
from app.services.inventory import inventory_service, transfer_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-xfer-cost"


async def _make_branch(db, name: str) -> tuple[Branch, Warehouse]:
    branch = Branch(
        name=f"{MARKER} {name}", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
    )
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Default stock", is_default=True)
    db.add(warehouse)
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
    return branch, warehouse


async def _receive(db, ids, user, *, quantity: str, unit_cost: str) -> None:
    """Lay a PURCHASING cost layer at ``unit_cost`` into the source warehouse."""
    txn = InventoryTransaction(
        reference=await inventory_service.next_reference(db, TxnType.PURCHASING.value),
        type=TxnType.PURCHASING.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=ids.source,
        warehouse_id=ids.source_wh,
        business_date="2026-09-22",
        creator_id=user.id,
        items=[
            InventoryTransactionItem(
                item_id=ids.item,
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
        source, source_wh = await _make_branch(db, "source")
        dest, dest_wh = await _make_branch(db, "dest")
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
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
        )
        db.add(item)
        await db.flush()
        # Both warehouses start empty; the source's stock is built from real
        # receipts so its FIFO layers (and their spread) are the ones under test.
        db.add(
            InventoryLevel(
                item_id=item.id, warehouse_id=source_wh.id, quantity=Decimal("0")
            )
        )
        db.add(
            InventoryLevel(
                item_id=item.id, warehouse_id=dest_wh.id, quantity=Decimal("0")
            )
        )
        await db.commit()
        ids = SimpleNamespace(
            source=source.id,
            dest=dest.id,
            source_wh=source_wh.id,
            dest_wh=dest_wh.id,
            user=user.id,
            item=item.id,
        )
    yield engine, Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        branch_ids = [ids.source, ids.dest]
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id.in_(branch_ids)
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            TransferOrder.__table__.delete().where(
                TransferOrder.source_branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(InventoryLevel.item_id == ids.item)
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id == ids.item)
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id.in_(branch_ids))
        )
        await db.execute(User.__table__.delete().where(User.id == ids.user))
        await db.execute(Branch.__table__.delete().where(Branch.id.in_(branch_ids)))
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


async def test_receive_carries_the_consumed_fifo_cost_not_the_average(env):
    _engine, Session, ids = env

    # Source builds 40 units across two layers: 20 @ 1.00, then 20 @ 4.00.
    # Blended average is 2.50, but the OLDEST layer — the one a transfer draws
    # first — is 1.00. The two figures must not be confused.
    async with Session() as db:
        user = await db.get(User, ids.user)
        await _receive(db, ids, user, quantity="20", unit_cost="1.00")
        await _receive(db, ids, user, quantity="20", unit_cost="4.00")
        await db.commit()

    async with Session() as db:
        assert await _level_cost(db, ids.item, ids.source_wh) == Decimal("2.5")

    # Transfer 20 units: the send consumes the oldest 20 @ 1.00.
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_transfer_order(
            db,
            source_branch=source,
            user=user,
            items=[
                SimpleNamespace(
                    item_id=ids.item,
                    unit="storage",
                    override=False,
                    allocations=[
                        SimpleNamespace(branch_id=ids.dest, quantity=Decimal("20"))
                    ],
                )
            ],
        )
        await db.commit()
        child_id = order.children[0].id

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        sent = await transfer_service.mark_transfer_sent(db, transfer=child, user=user)
        await db.commit()
        sent_id = sent.id

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        received = await transfer_service.receive_transfer(
            db, transfer=child, user=user
        )
        await db.commit()
        received_id = received.id

    async with Session() as db:
        sent_txn = await inventory_service.load_transaction(db, sent_id)
        received_txn = await inventory_service.load_transaction(db, received_id)

        # The source released 20 @ 1.00 = 20.00 of FIFO value...
        assert Decimal(str(sent_txn.total_cost)) == Decimal("20.00")
        # ...and the destination is valued at exactly that, not 20 @ 2.50 = 50.00.
        assert Decimal(str(received_txn.total_cost)) == Decimal("20.00")
        assert Decimal(str(received_txn.total_cost)) == Decimal(
            str(sent_txn.total_cost)
        )

        # Destination on-hand carries the released cost (1.00/unit), not the
        # source's blended average (2.50).
        assert await _level_cost(db, ids.item, ids.dest_wh) == Decimal("1")
