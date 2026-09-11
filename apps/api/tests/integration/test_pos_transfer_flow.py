"""Source-initiated transfers on the till, against a real Postgres.

The register pushes a transfer: it is created already accepted and shipped in one
step (source stock leaves now), and the receiving branch books what actually
arrived — short or over. The sent-vs-received difference is never silently
absorbed: the sender's stock stays down by what it shipped, the destination rises
by what it accepted, and the line records both with the receiver's reason.
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
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import TransferOrder, TransferOrderStatusEnum
from app.models.user import User
from app.services.inventory import inventory_service, transfer_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-transfer"


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
            cost=Decimal("3"),
        )
        db.add(item)
        await db.flush()
        # Seed opening stock at the source so there is something to ship.
        db.add(
            InventoryLevel(
                item_id=item.id,
                warehouse_id=source_wh.id,
                quantity=Decimal("100"),
                average_cost=Decimal("3"),
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


async def _level(db, item_id, warehouse_id) -> Decimal:
    level = (
        await db.execute(
            select(InventoryLevel).where(
                InventoryLevel.item_id == item_id,
                InventoryLevel.warehouse_id == warehouse_id,
            )
        )
    ).scalar_one()
    return Decimal(str(level.quantity))


def _line(item_id, quantity):
    return SimpleNamespace(
        item_id=item_id, quantity=Decimal(str(quantity)), unit="storage"
    )


async def test_create_and_send_ships_immediately_from_source(env):
    _engine, Session, ids = env
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 10)],
        )
        await db.commit()

    async with Session() as db:
        assert await _level(db, ids.item, ids.source_wh) == Decimal("90")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("0")
        reloaded = await transfer_service.load_transfer_order(db, order.id)
        assert reloaded.status == TransferOrderStatusEnum.ACCEPTED.value
        assert reloaded.sent_transaction_id is not None
        assert reloaded.received_transaction_id is None
        assert Decimal(str(reloaded.items[0].sent_quantity)) == Decimal("10")


async def test_short_receipt_leaves_the_loss_on_the_sender(env):
    _engine, Session, ids = env
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 10)],
        )
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        order = await transfer_service.load_transfer_order(db, order.id)
        line_id = order.items[0].id
        await transfer_service.receive_transfer(
            db,
            order=order,
            user=user,
            received={line_id: Decimal("8")},
            reasons={line_id: "two bags split in transit"},
        )
        await db.commit()

    async with Session() as db:
        # Source is down by what it shipped (10); dest up only by what arrived (8).
        # The 2-unit transit loss sits on the sender's -10 — nothing vanished.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("90")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("8")
        order = await transfer_service.load_transfer_order(db, order.id)
        assert order.status == TransferOrderStatusEnum.CLOSED.value
        assert Decimal(str(order.items[0].received_quantity)) == Decimal("8")
        assert order.items[0].variance_reason == "two bags split in transit"


async def test_over_receipt_is_allowed_and_credited_to_the_destination(env):
    _engine, Session, ids = env
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 5)],
        )
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        order = await transfer_service.load_transfer_order(db, order.id)
        line_id = order.items[0].id
        await transfer_service.receive_transfer(
            db,
            order=order,
            user=user,
            received={line_id: Decimal("7")},
            reasons={line_id: "found two extra in the crate"},
        )
        await db.commit()

    async with Session() as db:
        # Over-receipt is booked in full at the destination (a genuine gain there).
        assert await _level(db, ids.item, ids.source_wh) == Decimal("95")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("7")
        order = await transfer_service.load_transfer_order(db, order.id)
        assert Decimal(str(order.items[0].received_quantity)) == Decimal("7")


async def test_create_and_send_is_idempotent_on_client_request_id(env):
    _engine, Session, ids = env
    token = "till-token-abc"
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        first = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 10)],
            client_request_id=token,
        )
        await db.commit()

    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        # A retry with the same token must return the same order, not ship again.
        again = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 10)],
            client_request_id=token,
        )
        await db.commit()
        assert again.id == first.id

    async with Session() as db:
        # Source decremented exactly once (100 → 90), not twice.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("90")


async def test_return_ships_from_source_with_reasons(env):
    _engine, Session, ids = env
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        line = SimpleNamespace(
            item_id=ids.item,
            quantity=Decimal("4"),
            unit="storage",
            variance_reason="expired",
        )
        order = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[line],
            kind="return",
        )
        await db.commit()

    async with Session() as db:
        assert await _level(db, ids.item, ids.source_wh) == Decimal("96")
        order = await transfer_service.load_transfer_order(db, order.id)
        assert order.kind == "return"
        assert order.items[0].variance_reason == "expired"


async def test_transfer_to_a_non_pos_branch_auto_completes(env):
    _engine, Session, ids = env
    # Make the destination a branch that does not run the POS.
    async with Session() as db:
        dest = await db.get(Branch, ids.dest)
        dest.uses_pos = False
        await db.commit()

    async with Session() as db:
        source = await db.get(Branch, ids.source)
        dest = await db.get(Branch, ids.dest)
        user = await db.get(User, ids.user)
        order = await transfer_service.create_and_send(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[_line(ids.item, 6)],
        )
        await db.commit()

    async with Session() as db:
        # No till to receive it, so the shipment is booked straight in and closed.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("94")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("6")
        order = await transfer_service.load_transfer_order(db, order.id)
        assert order.status == TransferOrderStatusEnum.CLOSED.value
        assert order.received_transaction_id is not None
