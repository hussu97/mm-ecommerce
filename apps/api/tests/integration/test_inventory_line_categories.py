"""Category on the transfer/transaction line responses, and the admin receive
endpoint capturing a per-line variance reason — against a real Postgres.

Two clients group inventory lines by category (the POS create-transfer/returns
screens and the admin count detail), so the line responses carry the item's
category name and its display order, resolved up front rather than read off
``item.category`` lazily (which raises MissingGreenlet under asyncio). An
uncategorised item leaves both null so clients sort it last.

The admin receive endpoint books a transfer in and, when the receiver supplies a
reason for a short/over line, persists it on the line as ``variance_reason``.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import inventory as inventory_api
from app.api.v1 import operations as operations_api
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
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

MARKER = "pytest-line-cat"


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


def _make_item(name: str, category_id: uuid.UUID | None) -> InventoryItem:
    return InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        name=name,
        category_id=category_id,
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="kg",
        ingredient_unit="kg",
        storage_to_ingredient_factor=Decimal("1"),
        cost=Decimal("3"),
    )


@pytest.fixture
async def env():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        source, source_wh = await _make_branch(db, "source")
        dest, dest_wh = await _make_branch(db, "dest")
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_admin=True,
        )
        db.add(user)
        category = InventoryCategory(
            name=f"{MARKER} Dry goods",
            reference=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            display_order=7,
        )
        db.add(category)
        await db.flush()
        # One categorised item and one deliberately uncategorised.
        item = _make_item("Flour", category.id)
        loose = _make_item("Uncategorised salt", None)
        db.add(item)
        db.add(loose)
        await db.flush()
        for wh in (source_wh, dest_wh):
            for it in (item, loose):
                db.add(
                    InventoryLevel(
                        item_id=it.id,
                        warehouse_id=wh.id,
                        quantity=Decimal("100") if wh is source_wh else Decimal("0"),
                        average_cost=Decimal("3"),
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
            loose=loose.id,
            category=category.id,
        )
    yield engine, Session, ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        branch_ids = [ids.source, ids.dest]
        item_ids = [ids.item, ids.loose]
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
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_(item_ids)
            )
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id.in_(branch_ids)
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(InventoryItem.id.in_(item_ids))
        )
        await db.execute(
            InventoryCategory.__table__.delete().where(
                InventoryCategory.id == ids.category
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id.in_(branch_ids))
        )
        await db.execute(User.__table__.delete().where(User.id == ids.user))
        await db.execute(Branch.__table__.delete().where(Branch.id.in_(branch_ids)))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
    await engine.dispose()


def _line(item_id, quantity):
    return SimpleNamespace(
        item_id=item_id, quantity=Decimal(str(quantity)), unit="storage"
    )


async def test_transfer_line_serialises_with_category(env):
    """A transfer order line carries the item's category name + display order, and
    an uncategorised item's line leaves both null."""
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
            lines=[_line(ids.item, 5), _line(ids.loose, 3)],
        )
        await db.commit()

    async with Session() as db:
        order = await transfer_service.load_transfer_order(db, order.id)
        payload = await operations_api._serialise_transfer(db, order)
        by_item = {line.item_id: line for line in payload.items}

        categorised = by_item[ids.item]
        assert categorised.category_name == f"{MARKER} Dry goods"
        assert categorised.category_order == 7

        uncategorised = by_item[ids.loose]
        assert uncategorised.category_name is None
        assert uncategorised.category_order is None


async def test_transaction_line_serialises_with_category(env):
    """A transaction (count-detail) line carries the item's category, null for an
    uncategorised item."""
    _engine, Session, ids = env
    async with Session() as db:
        transaction = InventoryTransaction(
            reference=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            type="inventory_count",
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=ids.source,
            warehouse_id=ids.source_wh,
            business_date="2026-09-11",
            creator_id=ids.user,
        )
        db.add(transaction)
        await db.flush()
        for item_id in (ids.item, ids.loose):
            db.add(
                InventoryTransactionItem(
                    transaction_id=transaction.id,
                    item_id=item_id,
                    quantity=Decimal("1"),
                    unit="storage",
                    conversion_factor=Decimal("1"),
                    unit_cost=Decimal("3"),
                )
            )
        await db.commit()
        transaction_id = transaction.id

    async with Session() as db:
        transaction = await inventory_service.load_transaction(db, transaction_id)
        payload = await inventory_api._serialise_one_transaction(db, transaction)
        by_item = {line.item_id: line for line in payload.items}

        categorised = by_item[ids.item]
        assert categorised.category_name == f"{MARKER} Dry goods"
        assert categorised.category_order == 7

        uncategorised = by_item[ids.loose]
        assert uncategorised.category_name is None
        assert uncategorised.category_order is None


async def test_admin_receive_persists_variance_reason(env):
    """The admin receive endpoint records the receiver's per-line reason on the
    line when one is supplied."""
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
        order_id = order.id

    async with Session() as db:
        user = await db.get(User, ids.user)
        order = await transfer_service.load_transfer_order(db, order_id)
        line_id = order.items[0].id
        data = operations_api.AdminTransferReceive(
            lines=[
                operations_api.AdminTransferReceiveLine(
                    transfer_order_item_id=line_id,
                    quantity=Decimal("8"),
                    reason="two bags split in transit",
                )
            ]
        )
        await operations_api.receive_transfer(
            order_id=order_id, data=data, db=db, user=user
        )
        await db.commit()

    async with Session() as db:
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.status == TransferOrderStatusEnum.CLOSED.value
        assert Decimal(str(order.items[0].received_quantity)) == Decimal("8")
        assert order.items[0].variance_reason == "two bags split in transit"


async def test_admin_receive_empty_lines_accepts_all_as_sent(env):
    """An empty ``lines`` list receives every line as sent — the "force receive"
    case that needs no extra endpoint code."""
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
            lines=[_line(ids.item, 6)],
        )
        await db.commit()
        order_id = order.id

    async with Session() as db:
        user = await db.get(User, ids.user)
        await operations_api.receive_transfer(
            order_id=order_id,
            data=operations_api.AdminTransferReceive(lines=[]),
            db=db,
            user=user,
        )
        await db.commit()

    async with Session() as db:
        order = await transfer_service.load_transfer_order(db, order_id)
        assert order.status == TransferOrderStatusEnum.CLOSED.value
        # Received defaulted to the full sent quantity with no reason recorded.
        assert Decimal(str(order.items[0].received_quantity)) == Decimal("6")
        assert order.items[0].variance_reason is None
