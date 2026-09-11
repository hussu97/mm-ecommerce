"""Admin-created transfer orders fanning out to per-branch children, against a
real Postgres.

An admin raises one order from a source branch and allocates quantities to
destinations; it fans out into one child transfer each, and **nothing moves at
create time**. The source till then marks each child sent (its stock leaves), and
the destination books what actually arrived — short or over. The sent-vs-received
difference is never silently absorbed: the sender's stock stays down by what it
shipped, the destination rises by what it accepted, and the line records both
with the receiver's reason. Overriding the source's on-hand at create writes a
shortfall adjustment so the fan-out is covered.
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
        # Deleting the parent cascades to its children and their lines.
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


def _alloc(branch_id, quantity):
    return SimpleNamespace(branch_id=branch_id, quantity=Decimal(str(quantity)))


def _item(item_id, allocations, *, override=False, unit="storage"):
    return SimpleNamespace(
        item_id=item_id, unit=unit, override=override, allocations=allocations
    )


async def _order(db, ids, quantity, *, override=False):
    source = await db.get(Branch, ids.source)
    user = await db.get(User, ids.user)
    return await transfer_service.create_transfer_order(
        db,
        source_branch=source,
        user=user,
        items=[_item(ids.item, [_alloc(ids.dest, quantity)], override=override)],
    )


async def test_creating_an_order_moves_no_stock_until_it_is_sent(env):
    _engine, Session, ids = env
    async with Session() as db:
        order = await _order(db, ids, 10)
        await db.commit()
        order_id = order.id
        child_id = order.children[0].id

    async with Session() as db:
        # Pending: nothing has left the source yet.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("100")
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.status == TransferOrderStatusEnum.PENDING.value

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        await transfer_service.mark_transfer_sent(db, transfer=child, user=user)
        await db.commit()

    async with Session() as db:
        assert await _level(db, ids.item, ids.source_wh) == Decimal("90")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("0")
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.status == TransferOrderStatusEnum.SENT.value
        child = order.children[0]
        assert child.sent_transaction_id is not None
        assert child.received_transaction_id is None
        assert Decimal(str(child.items[0].sent_quantity)) == Decimal("10")


async def test_short_receipt_leaves_the_loss_on_the_sender(env):
    _engine, Session, ids = env
    async with Session() as db:
        order = await _order(db, ids, 10)
        await db.commit()
        child_id = order.children[0].id

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        await transfer_service.mark_transfer_sent(db, transfer=child, user=user)
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        line_id = child.items[0].id
        await transfer_service.receive_transfer(
            db,
            transfer=child,
            user=user,
            received={line_id: Decimal("8")},
            reasons={line_id: "two bags split in transit"},
        )
        await db.commit()

    async with Session() as db:
        # Source down by what it shipped (10); dest up only by what arrived (8).
        assert await _level(db, ids.item, ids.source_wh) == Decimal("90")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("8")
        child = await transfer_service.load_transfer(db, child_id)
        assert child.status == "closed"
        assert Decimal(str(child.items[0].received_quantity)) == Decimal("8")
        assert child.items[0].variance_reason == "two bags split in transit"


async def test_over_receipt_is_credited_to_the_destination(env):
    _engine, Session, ids = env
    async with Session() as db:
        order = await _order(db, ids, 5)
        await db.commit()
        child_id = order.children[0].id

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        await transfer_service.mark_transfer_sent(db, transfer=child, user=user)
        await db.commit()

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        line_id = child.items[0].id
        await transfer_service.receive_transfer(
            db,
            transfer=child,
            user=user,
            received={line_id: Decimal("7")},
            reasons={line_id: "found two extra in the crate"},
        )
        await db.commit()

    async with Session() as db:
        assert await _level(db, ids.item, ids.source_wh) == Decimal("95")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("7")


async def test_creating_an_order_is_idempotent_on_client_request_id(env):
    _engine, Session, ids = env
    token = "admin-token-abc"
    async with Session() as db:
        source = await db.get(Branch, ids.source)
        user = await db.get(User, ids.user)
        first = await transfer_service.create_transfer_order(
            db,
            source_branch=source,
            user=user,
            items=[_item(ids.item, [_alloc(ids.dest, 10)])],
            client_request_id=token,
        )
        await db.commit()
        first_id = first.id

    async with Session() as db:
        source = await db.get(Branch, ids.source)
        user = await db.get(User, ids.user)
        again = await transfer_service.create_transfer_order(
            db,
            source_branch=source,
            user=user,
            items=[_item(ids.item, [_alloc(ids.dest, 10)])],
            client_request_id=token,
        )
        await db.commit()
        assert again.id == first_id

    async with Session() as db:
        # Create moves no stock, and the second create did not raise a second order.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("100")
        count = (
            (
                await db.execute(
                    select(TransferOrder).where(
                        TransferOrder.source_branch_id == ids.source
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1


async def test_override_writes_a_shortfall_adjustment_to_cover_the_fan_out(env):
    _engine, Session, ids = env
    async with Session() as db:
        # Sending 120 with only 100 on hand, without override, is refused.
        with pytest.raises(BadRequestError):
            await _order(db, ids, 120)
        await db.rollback()

    async with Session() as db:
        order = await _order(db, ids, 120, override=True)
        await db.commit()
        order_id = order.id

    async with Session() as db:
        # The 20-unit shortfall was topped up, so on-hand now covers the fan-out.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("120")
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.adjustment_group_id is not None


async def test_return_ships_from_source_immediately(env):
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
        order = await transfer_service.create_return_order(
            db,
            source_branch=source,
            destination_branch=dest,
            user=user,
            lines=[line],
        )
        await db.commit()
        order_id = order.id

    async with Session() as db:
        # A return ships immediately, so the source is already down.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("96")
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.kind == "return"
        child = order.children[0]
        assert child.items[0].variance_reason == "expired"
        assert child.sent_transaction_id is not None


async def test_sending_to_a_non_pos_branch_auto_completes(env):
    _engine, Session, ids = env
    async with Session() as db:
        dest = await db.get(Branch, ids.dest)
        dest.uses_pos = False
        await db.commit()

    async with Session() as db:
        order = await _order(db, ids, 6)
        await db.commit()
        child_id = order.children[0].id

    async with Session() as db:
        user = await db.get(User, ids.user)
        child = await transfer_service.load_transfer(db, child_id)
        await transfer_service.mark_transfer_sent(db, transfer=child, user=user)
        await db.commit()

    async with Session() as db:
        # No till to receive it, so marking it sent books it straight in and closes.
        assert await _level(db, ids.item, ids.source_wh) == Decimal("94")
        assert await _level(db, ids.item, ids.dest_wh) == Decimal("6")
        child = await transfer_service.load_transfer(db, child_id)
        assert child.status == "closed"
        assert child.received_transaction_id is not None
