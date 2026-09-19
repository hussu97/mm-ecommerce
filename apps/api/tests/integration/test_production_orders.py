"""Transfer AND production orders, against a real Postgres.

An admin raises production lines at a source branch (with a transfer, or on their
own). **Nothing moves at create.** The source till then produces a line — which
posts the same PRODUCTION (+ CONSUMPTION_FROM_PRODUCTION) movement the shift
report's produce path posts, linked back to the line — or cancels it with a note
(moving nothing). The produced PRODUCTION movement is what the next finished-goods
report prefills from, so produced goods flow into the next count with no double
post.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError, ConflictError
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import (
    ProductionLine,
    ProductionLineStatusEnum,
    ProductionOrder,
    ProductionOrderStatusEnum,
    TransferOrder,
)
from app.models.user import User
from app.services.inventory import inventory_service, recipe_service, transfer_service
from app.services.inventory.recipe_service import RecipeLineInput

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
]

MARKER = "prodorder-test"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _item(db, name, kind, **kw):
    item = InventoryItem(
        sku=f"{MARKER}-{name}-{uuid.uuid4().hex[:8]}",
        name=name,
        kind=kind,
        tracking_mode="stocked",
        storage_unit=kw.get("unit", "unit"),
        ingredient_unit=kw.get("unit", "unit"),
        storage_to_ingredient_factor=Decimal("1"),
    )
    db.add(item)
    await db.flush()
    return item


@pytest.fixture
async def env(engine):
    """A source branch producing Brownie (2g flour/unit) and Cookie (1g flour/unit),
    plus a non-recipe Napkin, plus a destination branch for the transfer half."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        source = Branch(
            name=f"{MARKER} source", reference=f"{MARKER}-src-{uuid.uuid4().hex[:10]}"
        )
        dest = Branch(
            name=f"{MARKER} dest", reference=f"{MARKER}-dst-{uuid.uuid4().hex[:10]}"
        )
        db.add_all([source, dest])
        await db.flush()
        for b in (source, dest):
            db.add(Warehouse(branch_id=b.id, name="Default stock", is_default=True))
            db.add(
                BranchInventorySettings(
                    branch_id=b.id,
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
        await db.flush()

        flour = await _item(db, "Flour", "raw_material", unit="g", cost=Decimal("0.01"))
        brownie = await _item(db, "Brownie", "produced_good")
        cookie = await _item(db, "Cookie", "produced_good")
        napkin = await _item(db, "Napkin", "packaging")

        await recipe_service.draft_and_activate(
            db,
            kind="inventory_item",
            owner_id=brownie.id,
            lines=[RecipeLineInput(item_id=flour.id, quantity=Decimal("2"))],
            user_id=user.id,
        )
        await recipe_service.draft_and_activate(
            db,
            kind="inventory_item",
            owner_id=cookie.id,
            lines=[RecipeLineInput(item_id=flour.id, quantity=Decimal("1"))],
            user_id=user.id,
        )
        source_wh = await inventory_service.default_warehouse(db, source.id)
        inventory_service.apply_movement(
            await inventory_service.level_for(db, flour.id, source_wh.id),
            Decimal("1000"),
            Decimal("0.01"),
        )
        await db.commit()
        ids = SimpleNamespace(
            source=source,
            dest=dest,
            user=user,
            flour=flour.id,
            brownie=brownie.id,
            cookie=cookie.id,
            napkin=napkin.id,
            source_wh=source_wh.id,
        )
    yield ids, Session

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        for bid in (ids.source.id, ids.dest.id):
            await db.execute(
                ProductionLine.__table__.delete().where(
                    ProductionLine.production_order_id.in_(
                        select(ProductionOrder.id).where(
                            ProductionOrder.source_branch_id == bid
                        )
                    )
                )
            )
            await db.execute(
                ProductionOrder.__table__.delete().where(
                    ProductionOrder.source_branch_id == bid
                )
            )
            await db.execute(
                InventoryTransactionItem.__table__.delete().where(
                    InventoryTransactionItem.transaction_id.in_(
                        select(InventoryTransaction.id).where(
                            InventoryTransaction.branch_id == bid
                        )
                    )
                )
            )
            await db.execute(
                InventoryTransaction.__table__.delete().where(
                    InventoryTransaction.branch_id == bid
                )
            )
            await db.execute(
                InventoryLevel.__table__.delete().where(
                    InventoryLevel.warehouse_id.in_(
                        select(Warehouse.id).where(Warehouse.branch_id == bid)
                    )
                )
            )
        await db.commit()


async def _on_hand(db, item_id, warehouse_id) -> Decimal:
    level = await inventory_service.level_for(db, item_id, warehouse_id)
    return Decimal(str(level.quantity))


async def _count_txns(db, branch_id, txn_type) -> int:
    return (
        await db.scalar(
            select(text("count(*)"))
            .select_from(InventoryTransaction)
            .where(
                InventoryTransaction.branch_id == branch_id,
                InventoryTransaction.type == txn_type,
            )
        )
    ) or 0


async def test_create_moves_no_stock_then_produce_posts(env):
    ids, Session = env
    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            production_items=[
                SimpleNamespace(
                    item_id=ids.brownie, quantity=Decimal("5"), unit="storage"
                )
            ],
        )
        await db.commit()
        assert order.status == ProductionOrderStatusEnum.PENDING.value
        assert len(order.lines) == 1
        line_id = order.lines[0].id
        # No movement at create.
        assert (
            await _count_txns(
                db, ids.source.id, InventoryTransactionTypeEnum.PRODUCTION.value
            )
            == 0
        )

    async with Session() as db:
        line = await transfer_service.produce_line(db, line_id=line_id, user=ids.user)
        await db.commit()
        assert line.status == ProductionLineStatusEnum.PRODUCED.value
        assert line.produced_quantity == Decimal("5.0000")
        assert line.production_transaction_id is not None

    async with Session() as db:
        # Ledger: brownie up 5, flour down 10 (2g/unit).
        assert await _on_hand(db, ids.brownie, ids.source_wh) == Decimal("5.0000")
        assert await _on_hand(db, ids.flour, ids.source_wh) == Decimal("990.0000")
        assert (
            await _count_txns(
                db, ids.source.id, InventoryTransactionTypeEnum.PRODUCTION.value
            )
            == 1
        )
        assert (
            await _count_txns(
                db,
                ids.source.id,
                InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value,
            )
            == 1
        )
        order = await transfer_service.load_production_order(
            db, line.production_order_id
        )
        assert order.status == ProductionOrderStatusEnum.PRODUCED.value
        # The PRODUCTION movement links back to the line.
        txn = await db.get(InventoryTransaction, line.production_transaction_id)
        assert txn.source_type == "production_line"
        assert txn.source_id == str(line_id)

    # Re-produce guard.
    async with Session() as db:
        with pytest.raises(ConflictError):
            await transfer_service.produce_line(db, line_id=line_id, user=ids.user)


async def test_batch_basis_line_stores_basis_and_moves_owner_units(env):
    """A batch-basis recipe: the client sends OWNER units (24 = 2 batches × 12);
    the line snapshots the basis + yield and derives the basis count (2) for
    display, and the ledger moves owner units."""
    ids, Session = env
    # Re-activate cookie as a batch recipe: 1 batch = 12 cookies, 6g flour/batch.
    async with Session() as db:
        draft = await recipe_service.create_draft(
            db,
            kind="inventory_item",
            owner_id=ids.cookie,
            lines=[RecipeLineInput(item_id=ids.flour, quantity=Decimal("6"))],
            basis="batch",
            batch_yield=Decimal("12"),
        )
        await recipe_service.activate(db, version_id=draft.id, user_id=ids.user.id)
        await db.commit()

    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            production_items=[
                # 2 batches, sent as 24 owner units (the client converts).
                SimpleNamespace(
                    item_id=ids.cookie, quantity=Decimal("24"), unit="storage"
                )
            ],
        )
        await db.commit()
        line = order.lines[0]
        line_id = line.id
        assert line.basis == "batch"
        assert line.batch_yield == Decimal("12.00000000")
        # Owner-unit truth 24; basis count derived as 24 / 12 = 2.
        assert line.planned_basis_quantity == Decimal("2.0000")
        assert line.planned_quantity == Decimal("24.0000")

    async with Session() as db:
        line = await transfer_service.produce_line(db, line_id=line_id, user=ids.user)
        await db.commit()
        # Owner units on the ledger side, batches on the basis side.
        assert line.produced_quantity == Decimal("24.0000")
        assert line.produced_basis_quantity == Decimal("2.0000")

    async with Session() as db:
        # Cookie up 24 owner units; flour down 12g (6g/batch * 2 batches).
        assert await _on_hand(db, ids.cookie, ids.source_wh) == Decimal("24.0000")
        assert await _on_hand(db, ids.flour, ids.source_wh) == Decimal("988.0000")


async def test_batch_basis_produce_override_is_in_owner_units(env):
    """An override at produce time is in owner units (the till converts a batch
    count to units before sending); the basis count is derived for display."""
    ids, Session = env
    async with Session() as db:
        draft = await recipe_service.create_draft(
            db,
            kind="inventory_item",
            owner_id=ids.cookie,
            lines=[RecipeLineInput(item_id=ids.flour, quantity=Decimal("6"))],
            basis="batch",
            batch_yield=Decimal("12"),
        )
        await recipe_service.activate(db, version_id=draft.id, user_id=ids.user.id)
        await db.commit()

    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            production_items=[
                # 3 batches planned, sent as 36 owner units.
                SimpleNamespace(
                    item_id=ids.cookie, quantity=Decimal("36"), unit="storage"
                )
            ],
        )
        await db.commit()
        line_id = order.lines[0].id

    async with Session() as db:
        # The till made only 1 batch (12 units), not the 3 planned.
        line = await transfer_service.produce_line(
            db, line_id=line_id, user=ids.user, quantity=Decimal("12")
        )
        await db.commit()
        assert line.produced_quantity == Decimal("12.0000")  # owner units
        assert line.produced_basis_quantity == Decimal("1.0000")  # 12 / 12 = 1 batch

    async with Session() as db:
        assert await _on_hand(db, ids.cookie, ids.source_wh) == Decimal("12.0000")


async def test_partial_then_produce_all(env):
    ids, Session = env
    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            production_items=[
                SimpleNamespace(
                    item_id=ids.brownie, quantity=Decimal("3"), unit="storage"
                ),
                SimpleNamespace(
                    item_id=ids.cookie, quantity=Decimal("4"), unit="storage"
                ),
            ],
        )
        await db.commit()
        order_id = order.id
        first_line = order.lines[0].id

    async with Session() as db:
        await transfer_service.produce_line(db, line_id=first_line, user=ids.user)
        await db.commit()
        order = await transfer_service.load_production_order(db, order_id)
        assert order.status == ProductionOrderStatusEnum.PARTIALLY_PRODUCED.value

    async with Session() as db:
        order = await transfer_service.produce_all(db, order_id=order_id, user=ids.user)
        await db.commit()
        assert order.status == ProductionOrderStatusEnum.PRODUCED.value
        assert all(
            ln.status == ProductionLineStatusEnum.PRODUCED.value for ln in order.lines
        )


async def test_cancel_requires_note_and_moves_nothing(env):
    ids, Session = env
    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            production_items=[
                SimpleNamespace(
                    item_id=ids.brownie, quantity=Decimal("2"), unit="storage"
                )
            ],
        )
        await db.commit()
        line_id = order.lines[0].id

    async with Session() as db:
        with pytest.raises(BadRequestError):
            await transfer_service.cancel_production_line(
                db, line_id=line_id, user=ids.user, note="  "
            )

    async with Session() as db:
        line = await transfer_service.cancel_production_line(
            db, line_id=line_id, user=ids.user, note="Out of flour"
        )
        await db.commit()
        assert line.status == ProductionLineStatusEnum.CANCELLED.value
        assert line.cancel_note == "Out of flour"
        assert (
            await _count_txns(
                db, ids.source.id, InventoryTransactionTypeEnum.PRODUCTION.value
            )
            == 0
        )
        order = await transfer_service.load_production_order(
            db, line.production_order_id
        )
        # Only line cancelled ⇒ whole order cancelled.
        assert order.status == ProductionOrderStatusEnum.CANCELLED.value


async def test_non_recipe_item_rejected(env):
    ids, Session = env
    async with Session() as db:
        with pytest.raises(BadRequestError):
            await transfer_service.create_production_order(
                db,
                source_branch=ids.source,
                user=ids.user,
                production_items=[
                    SimpleNamespace(
                        item_id=ids.napkin, quantity=Decimal("1"), unit="storage"
                    )
                ],
            )


async def test_combined_transfer_and_production_links(env):
    ids, Session = env
    async with Session() as db:
        order = await transfer_service.create_transfer_order(
            db,
            source_branch=ids.source,
            user=ids.user,
            items=[
                SimpleNamespace(
                    item_id=ids.flour,
                    unit="storage",
                    override=False,
                    allocations=[
                        SimpleNamespace(branch_id=ids.dest.id, quantity=Decimal("10"))
                    ],
                )
            ],
            production_items=[
                SimpleNamespace(
                    item_id=ids.brownie, quantity=Decimal("2"), unit="storage"
                )
            ],
        )
        await db.commit()
        # Returned a transfer order with a linked production order.
        assert isinstance(order, TransferOrder)
        prod = (
            await db.execute(
                select(ProductionOrder).where(
                    ProductionOrder.transfer_order_id == order.id
                )
            )
        ).scalar_one()
        assert len(prod.lines) == 1
        # No production movement at create.
        assert (
            await _count_txns(
                db, ids.source.id, InventoryTransactionTypeEnum.PRODUCTION.value
            )
            == 0
        )
