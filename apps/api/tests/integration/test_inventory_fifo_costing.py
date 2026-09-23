"""FIFO cost layers, end to end against a real Postgres.

The moving-average engine is gone: a receipt lays down a cost layer, an issue
consumes the oldest layers first and books their actual cost, and the level's
average is derived from what remains. These pin the arithmetic the brief spelled
out (300 g @ 10 then 200 g @ 5; consume 100, then 350) plus the edge cases —
positive adjustments priced off the oldest layer, void restoring exact layers,
backfilling uncosted opening stock, and the deterministic rebuild.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryItem,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.services.inventory import costing_service, inventory_service, ledger_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-fifo"
D = Decimal


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    """A live branch with a default warehouse, settings, user and a raw item."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        warehouse = Warehouse(branch_id=branch.id, name="Default", is_default=True)
        db.add(warehouse)
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                go_live_at=utcnow(),
                go_live_sequence=0,
            )
        )
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Butter",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="gram",
            ingredient_unit="gram",
            storage_to_ingredient_factor=D("1"),
        )
        db.add(item)
        await db.commit()
        ids = (branch.id, warehouse.id, user.id, item.id)

    yield ids

    branch_id, _, user_id, item_id = ids
    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        line_ids = select(InventoryTransactionItem.id).where(
            InventoryTransactionItem.transaction_id.in_(
                select(InventoryTransaction.id).where(
                    InventoryTransaction.branch_id == branch_id
                )
            )
        )
        await db.execute(
            InventoryCostLayerConsumption.__table__.delete().where(
                InventoryCostLayerConsumption.consuming_line_id.in_(line_ids)
            )
        )
        await db.execute(
            InventoryCostLayer.__table__.delete().where(
                InventoryCostLayer.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryLineCost.__table__.delete().where(
                InventoryLineCost.branch_id == branch_id
            )
        )
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
            InventoryItem.__table__.delete().where(InventoryItem.id == item_id)
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def _post(
    db,
    *,
    branch_id,
    warehouse_id,
    item_id,
    user,
    kind: InventoryTransactionTypeEnum,
    quantity,
    unit_cost="0",
):
    """Build and post one single-line transaction; returns the posted row."""
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, kind.value),
        type=kind.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        business_date="2026-09-18",
        creator_id=user.id,
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=D(str(quantity)),
                unit="storage",
                conversion_factor=D("1"),
                unit_cost=D(str(unit_cost)),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    return await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )


async def _level(db, item_id, warehouse_id) -> InventoryLevel:
    return await inventory_service.level_for(db, item_id, warehouse_id)


async def _remaining_layers(db, item_id, warehouse_id):
    rows = (
        (
            await db.execute(
                select(InventoryCostLayer)
                .where(
                    InventoryCostLayer.item_id == item_id,
                    InventoryCostLayer.warehouse_id == warehouse_id,
                    InventoryCostLayer.remaining_quantity > 0,
                )
                .order_by(
                    InventoryCostLayer.posting_sequence,
                    InventoryCostLayer.layer_index,
                )
            )
        )
        .scalars()
        .all()
    )
    return [(D(str(r.remaining_quantity)), D(str(r.unit_cost))) for r in rows]


async def test_the_brief_example_prices_fifo_and_shows_remaining_average(engine, env):
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        P = InventoryTransactionTypeEnum.PURCHASING
        C = InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS

        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="300",
            unit_cost="10",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="200",
            unit_cost="5",
        )
        level = await _level(db, item_id, warehouse_id)
        # Shown average = remaining value / remaining qty = (3000 + 1000) / 500 = 8.
        assert level.quantity == D("500.0000")
        assert level.average_cost == D("8.000000")

        # Consume 100 → all from the 10-priced layer, COGS 1000.
        consume1 = await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=C,
            quantity="100",
        )
        assert Decimal(str(consume1.items[0].total_cost)) == D("1000.00")
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("200.000000"), D("10.000000")),
            (D("200.000000"), D("5.000000")),
        ]
        level = await _level(db, item_id, warehouse_id)
        # remaining (2000 + 1000) / 400 = 7.5
        assert level.average_cost == D("7.500000")

        # Consume 350 → 200 @ 10 then 150 @ 5 = 2000 + 750 = 2750.
        consume2 = await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=C,
            quantity="350",
        )
        assert Decimal(str(consume2.items[0].total_cost)) == D("2750.00")
        # 50 g left, all at 5.
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("50.000000"), D("5.000000")),
        ]
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("50.0000")
        assert level.average_cost == D("5.000000")


async def test_positive_adjustment_is_priced_at_the_oldest_surviving_layer(engine, env):
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        P = InventoryTransactionTypeEnum.PURCHASING
        A = InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="100",
            unit_cost="4",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="100",
            unit_cost="9",
        )
        # A positive adjustment tops up at the earliest surviving cost (4), not 9.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=A,
            quantity="50",
        )
        layers = await _remaining_layers(db, item_id, warehouse_id)
        assert (D("50.000000"), D("4.000000")) in layers
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("250.0000")


async def test_void_restores_the_exact_layers_it_consumed(engine, env):
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        P = InventoryTransactionTypeEnum.PURCHASING
        C = InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="100",
            unit_cost="10",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="100",
            unit_cost="6",
        )
        consume = await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=C,
            quantity="120",
        )
        # 100 @ 10 + 20 @ 6 = 1120.
        assert Decimal(str(consume.items[0].total_cost)) == D("1120.00")
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("80.0000")

        await ledger_service.reverse_transaction(
            db, transaction_id=consume.id, user=user, reason="void"
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("200.0000")
        # Both layers whole again, oldest first at their original costs.
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("100.000000"), D("10.000000")),
            (D("100.000000"), D("6.000000")),
        ]
        assert level.average_cost == D("8.000000")


async def test_receipt_onto_negative_stock_keeps_layers_matching_quantity(engine, env):
    """Over-issue then receive: the incoming stock first cancels the shortfall, so
    Σ(remaining layers) tracks the level quantity instead of laying a phantom
    layer over a still-negative balance."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        P = InventoryTransactionTypeEnum.PURCHASING
        C = InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS
        # Issue with nothing on hand → negative balance, no layers.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=C,
            quantity="10",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("-10.0000")
        assert await _remaining_layers(db, item_id, warehouse_id) == []

        # Receive 4 — still net negative (-6): no layer is laid.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="4",
            unit_cost="5",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("-6.0000")
        assert await _remaining_layers(db, item_id, warehouse_id) == []

        # Receive 10 — now net +4: exactly the surplus becomes a layer.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="10",
            unit_cost="5",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("4.0000")
        layers = await _remaining_layers(db, item_id, warehouse_id)
        assert sum((q for q, _ in layers), D("0")) == D("4.000000")


async def test_a_level_with_no_ledger_behind_it_is_restated_to_the_ledger(engine, env):
    """v3: the ledger is the only source of stock. A level edited by hand (the
    old "legacy on-hand with no cost history") is drift, and the first posting
    that needs a replay restates it to what the ledger says."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        level = await _level(db, item_id, warehouse_id)
        level.quantity = D("40.0000")
        level.average_cost = D("0")
        await db.flush()

        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.PURCHASING,
            quantity="60",
            unit_cost="2",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("60.0000")
        assert level.average_cost == D("2")
        total_remaining, _ = await _sum_layers(db, item_id, warehouse_id)
        assert total_remaining == D("60.000000")


async def test_count_overage_onto_negative_stock_leaves_no_phantom_layer(engine, env):
    """The Cream Cheese bug: production ran the level to −1000, the close count
    found 0 on the shelf (+1000), and the old engine laid a 1000 g layer anyway."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS,
            quantity="1000",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.INVENTORY_COUNT,
            quantity="20",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("20.0000")
        total_remaining, _ = await _sum_layers(db, item_id, warehouse_id)
        assert total_remaining == D("20.000000")


async def test_a_voided_po_prices_nothing_and_the_next_po_prices_everything(
    engine, env
):
    """PO-003938 at Sharjah: keyed in packs (4 @ 29), voided, re-keyed as 2000 g
    @ 0.058. The void must take its price with it — the found stock and the
    shortfall consumed before any PO take the real one, 0.058."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        sale = await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS,
            quantity="375",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.INVENTORY_COUNT,
            quantity="4",
        )
        bad = await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.PURCHASING,
            quantity="4",
            unit_cost="29",
        )
        await ledger_service.reverse_transaction(
            db, transaction_id=bad.id, user=user, reason="keyed in packs"
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.PURCHASING,
            quantity="2000",
            unit_cost="0.058",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("2004.0000")
        assert level.average_cost == D("0.058")
        assert {
            cost for _, cost in await _remaining_layers(db, item_id, warehouse_id)
        } == {D("0.058")}
        sale_cost = await db.get(InventoryLineCost, sale.items[0].id)
        assert D(str(sale_cost.total_cost)) == D("21.7500")  # 375 × 0.058
        assert not sale_cost.is_provisional


async def test_an_estate_replay_after_live_postings_changes_nothing(engine, env):
    """The fast path, a warehouse replay and an estate replay are one engine: once
    postings have landed, replaying the whole estate must find nothing to fix."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        steps = [
            (InventoryTransactionTypeEnum.PURCHASING, "100", "2"),
            (InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS, "30", "0"),
            (InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS, "90", "0"),
            (InventoryTransactionTypeEnum.INVENTORY_COUNT, "5", "0"),
            (InventoryTransactionTypeEnum.PURCHASING, "50", "3"),
            (InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS, "10", "0"),
        ]
        for kind, qty, cost in steps:
            await _post(
                db,
                branch_id=branch_id,
                warehouse_id=warehouse_id,
                item_id=item_id,
                user=user,
                kind=kind,
                quantity=qty,
                unit_cost=cost,
            )
        await db.commit()
    async with Session() as db:
        await costing_service.replay_estate(db, wait=True)
        await db.commit()
    async with Session() as db:
        again = await costing_service.replay_estate(db, wait=True)
        assert again.changes == 0
        await db.rollback()


async def _sum_layers(db, item_id, warehouse_id):
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(InventoryCostLayer.remaining_quantity), 0),
                func.coalesce(
                    func.sum(
                        InventoryCostLayer.remaining_quantity
                        * InventoryCostLayer.unit_cost
                    ),
                    0,
                ),
            ).where(
                InventoryCostLayer.item_id == item_id,
                InventoryCostLayer.warehouse_id == warehouse_id,
                InventoryCostLayer.remaining_quantity > 0,
            )
        )
    ).one()
    return D(str(row[0])), D(str(row[1]))


async def test_first_purchase_prices_tracked_zero_cost_stock(engine, env):
    """The eggs/whipping-cream case: stock that already has *layers*, all at zero.

    Go-live seeds a raw ingredient with zero-cost opening / count layers (a real
    layer exists, so ``backfill`` finds no untracked gap to fill). The first
    priced purchase must still lift the surviving zero-cost stock to that cost —
    otherwise the old units sit at zero and are consumed at zero COGS before the
    priced stock is ever touched. Only what is still on hand is lifted; stock
    already issued at zero stays issued at zero. A rebuild reproduces it.
    """
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        # Go-live opening balance keyed with no price: a zero-cost *layer* exists.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.OPENING_BALANCE,
            quantity="100",
            unit_cost="0",
        )
        # 40 consumed before any cost was known — booked at zero, and must stay so.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS,
            quantity="40",
            unit_cost="0",
        )
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("60.000000"), D("0.000000")),
        ]

        # First priced PO: 50 @ 3. The 60 surviving zero-cost units are lifted to
        # 3, then the 50 received join them — 110 units, all at 3.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.PURCHASING,
            quantity="50",
            unit_cost="3",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("110.0000")
        assert level.average_cost == D("3.000000")
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("60.000000"), D("3.000000")),
            (D("50.000000"), D("3.000000")),
        ]

        # A second priced PO must NOT re-price anything — value already exists.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.PURCHASING,
            quantity="10",
            unit_cost="9",
        )
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("60.000000"), D("3.000000")),
            (D("50.000000"), D("3.000000")),
            (D("10.000000"), D("9.000000")),
        ]

        # The lift lives in the forward path, so a rebuild reproduces it: no drift.
        drifts = await ledger_service.reconcile_levels(
            db, branch_id=branch_id, apply=False
        )
        assert [d for d in drifts if d.item_id == item_id] == []
        await db.rollback()


async def test_opening_balance_with_a_cost_lays_a_layer_at_that_cost(engine, env):
    """Cost is FIFO: an item carries no catalogue fallback any more, so an opening
    balance is valued at the cost it is keyed with. Entering a real go-live cost
    lays a layer at that cost; a 0-cost opening balance stays 0 (unknown until a
    priced receipt), which the rebuild reproduces without drift."""
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        # Opening balance keyed with its known go-live cost.
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=InventoryTransactionTypeEnum.OPENING_BALANCE,
            quantity="40",
            unit_cost="7",
        )
        level = await _level(db, item_id, warehouse_id)
        assert level.quantity == D("40.0000")
        assert level.average_cost == D("7.000000")
        assert await _remaining_layers(db, item_id, warehouse_id) == [
            (D("40.000000"), D("7.000000")),
        ]
        # A rebuild reproduces the same valuation — no drift.
        drifts = await ledger_service.reconcile_levels(
            db, branch_id=branch_id, apply=False
        )
        assert [d for d in drifts if d.item_id == item_id] == []
        await db.rollback()


async def test_rebuild_reproduces_layers_and_reports_no_drift(engine, env):
    branch_id, warehouse_id, user_id, item_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        P = InventoryTransactionTypeEnum.PURCHASING
        C = InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="300",
            unit_cost="10",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=P,
            quantity="200",
            unit_cost="5",
        )
        await _post(
            db,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            item_id=item_id,
            user=user,
            kind=C,
            quantity="350",
        )
        await db.commit()

    async with Session() as db:
        before = await _remaining_layers(db, item_id, warehouse_id)
        # A dry run must find no drift.
        drifts = await ledger_service.reconcile_levels(
            db, branch_id=branch_id, apply=False
        )
        assert [d for d in drifts if d.item_id == item_id] == []
        # And an apply must reproduce the identical surviving layers.
        await ledger_service.reconcile_levels(db, branch_id=branch_id, apply=True)
        after = await _remaining_layers(db, item_id, warehouse_id)
        assert before == after
        await db.rollback()
