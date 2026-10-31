"""Deferred FIFO replays, end to end against a real Postgres.

A posting the fast path can only cost by re-costing history (a receipt that
prices waiting stock, an inflow onto negative stock, stock of unknown cost, an
overage over provisional stock) used to replay its whole warehouse inside the
till's request. It now books what the fast path knows, queues the warehouse in
``inventory_costing_settle``, and the settle loop replays it seconds later.

The property that makes that safe is pinned here: the same ledger posted
through the inline path and through the deferred path, into two fresh
branches, settles to the *identical* projection — every line cost, layer,
consumption and level. Plus: a request replays nothing inline however many
postings defer (produce-all), the settle loop clears what it settles and keeps
what it could not, and the estate sweep is its backstop.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostingDirty,
    InventoryCostingSettle,
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryItem,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    Supplier,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import ProductionLine, ProductionOrder
from app.models.user import User
from app.services.inventory import (
    costing_service,
    inventory_service,
    recipe_service,
    transfer_service,
)
from app.services.inventory.recipe_service import RecipeLineInput
from tests.integration._stock import seed_stock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
]

MARKER = "deferred-replay"
D = Decimal
T = InventoryTransactionTypeEnum


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _branch(db, label: str, user: User) -> SimpleNamespace:
    """A trading branch with its own raw materials and made goods.

    Every branch gets its own items: the replay's last-resort estimate is the
    latest purchase-order price of the item *anywhere*, so two branches sharing
    an item would see each other's receipts and could not be compared.
    """
    branch = Branch(
        name=f"{MARKER} {label}", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
    )
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Default", is_default=True)
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
    items = {}
    for name, kind, unit in (
        ("Flour", "raw_material", "gram"),
        ("Butter", "raw_material", "gram"),
        ("Brownie", "produced_good", "unit"),
        ("Cookie", "produced_good", "unit"),
    ):
        item = InventoryItem(
            sku=f"{MARKER}-{name}-{uuid.uuid4().hex[:8]}",
            name=name,
            kind=kind,
            tracking_mode="stocked",
            storage_unit=unit,
            ingredient_unit=unit,
            storage_to_ingredient_factor=D("1"),
        )
        db.add(item)
        await db.flush()
        items[name] = item.id
    await recipe_service.draft_and_activate(
        db,
        kind="inventory_item",
        owner_id=items["Brownie"],
        lines=[
            RecipeLineInput(item_id=items["Flour"], quantity=D("2")),
            RecipeLineInput(item_id=items["Butter"], quantity=D("3")),
        ],
        user_id=user.id,
    )
    await recipe_service.draft_and_activate(
        db,
        kind="inventory_item",
        owner_id=items["Cookie"],
        lines=[RecipeLineInput(item_id=items["Butter"], quantity=D("1"))],
        user_id=user.id,
    )
    supplier = Supplier(name=f"{MARKER} supplier {uuid.uuid4().hex[:6]}")
    db.add(supplier)
    await db.flush()
    po = PurchaseOrder(
        reference=f"PO-{MARKER}-{uuid.uuid4().hex[:8]}",
        status="closed",
        supplier_id=supplier.id,
        branch_id=branch.id,
        warehouse_id=warehouse.id,
        business_date="2026-11-01",
    )
    db.add(po)
    await db.flush()
    return SimpleNamespace(
        branch=branch,
        wh=warehouse.id,
        items=items,
        names={v: k for k, v in items.items()},
        po=po.id,
        supplier=supplier.id,
    )


@pytest.fixture
async def env(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        await db.flush()
        inline = await _branch(db, "inline", user)
        deferred = await _branch(db, "deferred", user)
        await db.commit()
    yield SimpleNamespace(user=user, inline=inline, deferred=deferred), Session

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        for b in (inline, deferred):
            txns = select(InventoryTransaction.id).where(
                InventoryTransaction.branch_id == b.branch.id
            )
            line_ids = select(InventoryTransactionItem.id).where(
                InventoryTransactionItem.transaction_id.in_(txns)
            )
            for table, column in (
                (InventoryCostingSettle, InventoryCostingSettle.warehouse_id),
                (InventoryCostingDirty, InventoryCostingDirty.warehouse_id),
                (InventoryLevel, InventoryLevel.warehouse_id),
            ):
                await db.execute(table.__table__.delete().where(column == b.wh))
            await db.execute(
                InventoryCostLayerConsumption.__table__.delete().where(
                    InventoryCostLayerConsumption.consuming_line_id.in_(line_ids)
                )
            )
            await db.execute(
                InventoryCostLayer.__table__.delete().where(
                    InventoryCostLayer.branch_id == b.branch.id
                )
            )
            await db.execute(
                InventoryLineCost.__table__.delete().where(
                    InventoryLineCost.branch_id == b.branch.id
                )
            )
            await db.execute(
                InventoryTransactionItem.__table__.delete().where(
                    InventoryTransactionItem.transaction_id.in_(txns)
                )
            )
            await db.execute(
                InventoryTransaction.__table__.delete().where(
                    InventoryTransaction.branch_id == b.branch.id
                )
            )
            await db.execute(
                ProductionLine.__table__.delete().where(
                    ProductionLine.production_order_id.in_(
                        select(ProductionOrder.id).where(
                            ProductionOrder.source_branch_id == b.branch.id
                        )
                    )
                )
            )
            await db.execute(
                ProductionOrder.__table__.delete().where(
                    ProductionOrder.source_branch_id == b.branch.id
                )
            )
            await db.execute(
                PurchaseOrder.__table__.delete().where(PurchaseOrder.id == b.po)
            )
            await db.execute(
                Supplier.__table__.delete().where(Supplier.id == b.supplier)
            )
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def _post(db, b, user, kind, item, quantity, unit_cost="0", **kw):
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, kind.value),
        type=kind.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=b.branch.id,
        warehouse_id=b.wh,
        business_date="2026-11-01",
        creator_id=user.id,
        items=[
            InventoryTransactionItem(
                item_id=b.items[item],
                quantity=D(str(quantity)),
                unit="storage",
                conversion_factor=D("1"),
                unit_cost=D(str(unit_cost)),
            )
        ],
        **kw,
    )
    db.add(transaction)
    await db.flush()
    return await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )


async def _produce(db, b, user, item, quantity):
    return await transfer_service.produce(
        db,
        branch=b.branch,
        user=user,
        item_id=b.items[item],
        quantity=D(str(quantity)),
        warehouse_id=b.wh,
    )


async def _scenario(Session, b, user, *, settle_midway: bool) -> None:
    """A shop day that hits every deferrable refusal, in separate requests.

    Sales run ahead of deliveries and production (shortfalls), a count lands on
    negative stock, a receipt with no price adds stock of unknown cost, and the
    receipts and batches that follow price all of it — with sales drawing on
    the re-costed stock *before* any settle, the window the deferral opens.
    """
    async with Session() as db:
        await seed_stock(
            db,
            branch_id=b.branch.id,
            warehouse_id=b.wh,
            item_id=b.items["Flour"],
            quantity="1000",
            unit_cost="0.01",
        )
        await db.commit()
    async with Session() as db:
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Brownie", "5")
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Cookie", "2")
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Butter", "100")
        await db.commit()
    async with Session() as db:
        # A count onto negative stock with no layer to price it from.
        await _post(db, b, user, T.INVENTORY_COUNT, "Butter", "50")
        await db.commit()
    async with Session() as db:
        # Shift-report "Received": stock of unknown cost.
        await _post(
            db,
            b,
            user,
            T.PURCHASING,
            "Butter",
            "40",
            source_type="shift_inventory_report",
        )
        await db.commit()
    async with Session() as db:
        # Found stock while every layer on the shelf is still provisional.
        await _post(db, b, user, T.INVENTORY_COUNT, "Butter", "100")
        await db.commit()
    async with Session() as db:
        # The delivery that prices every butter estimate above.
        await _post(
            db, b, user, T.PURCHASING, "Butter", "500", "0.04", purchase_order_id=b.po
        )
        await db.commit()
    async with Session() as db:
        # Sold before it was priced by a settle: the deferral's window.
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Butter", "30")
        await db.commit()
    if settle_midway:
        async with Session() as db:
            await costing_service.settle_marked(db, budget_seconds=600)
    async with Session() as db:
        # A batch onto negative stock whose earlier sales wait on its price.
        await _produce(db, b, user, "Brownie", "8")
        await db.commit()
    async with Session() as db:
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Brownie", "2")
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Flour", "2000")
        await db.commit()
    async with Session() as db:
        order = await transfer_service.create_production_order(
            db,
            source_branch=b.branch,
            user=user,
            production_items=[
                SimpleNamespace(
                    item_id=b.items["Brownie"], quantity=D("3"), unit="storage"
                ),
                SimpleNamespace(
                    item_id=b.items["Cookie"], quantity=D("4"), unit="storage"
                ),
            ],
        )
        await db.commit()
        order_id = order.id
    async with Session() as db:
        await transfer_service.produce_all(db, order_id=order_id, user=user)
        await db.commit()
    async with Session() as db:
        # A count overage over flour that is all shortfall (negative), then the
        # receipt that prices it.
        await _post(db, b, user, T.INVENTORY_COUNT, "Flour", "25")
        await _post(
            db, b, user, T.PURCHASING, "Flour", "300", "0.02", purchase_order_id=b.po
        )
        await db.commit()
    async with Session() as db:
        # Butter oversold again, then a delivery prices those sales — the
        # everyday case — left unsettled at the end of the day's postings.
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Butter", "2000")
        await db.commit()
    async with Session() as db:
        await _post(
            db, b, user, T.PURCHASING, "Butter", "100", "0.05", purchase_order_id=b.po
        )
        await db.commit()


def _sorted(rows) -> list:
    """Rows hold None (no pricing line, a shortfall's layer): sort on repr."""
    return sorted(rows, key=repr)


async def _snapshot(db, b) -> dict:
    """The warehouse's projection, keyed so two branches can be compared.

    Ids differ between branches (they are derived from line ids), so every row
    is keyed by its transaction's place in the branch's posting order and its
    item's name instead.
    """
    rows = (
        await db.execute(
            select(
                InventoryTransactionItem.id,
                InventoryTransaction.posting_sequence,
                InventoryTransactionItem.item_id,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryTransactionItem.transaction_id,
            )
            .where(
                InventoryTransaction.branch_id == b.branch.id,
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
            )
        )
    ).all()
    sequences = sorted({row.posting_sequence for row in rows})
    rank = {seq: index for index, seq in enumerate(sequences)}
    key = {
        row.id: (rank[row.posting_sequence], b.names.get(row.item_id)) for row in rows
    }

    def k(line_id):
        return key.get(line_id) if line_id is not None else None

    line_costs = _sorted(
        (
            k(r.line_id),
            D(str(r.quantity)),
            D(str(r.unit_cost)),
            D(str(r.total_cost)),
            r.is_provisional,
            k(r.priced_by_line_id),
            D(str(r.running_quantity)),
            D(str(r.running_value)),
            r.superseded,
        )
        for r in (
            await db.execute(
                select(InventoryLineCost).where(InventoryLineCost.warehouse_id == b.wh)
            )
        )
        .scalars()
        .all()
    )
    layers = _sorted(
        (
            k(r.source_line_id),
            r.layer_index,
            r.source_kind,
            D(str(r.original_quantity)),
            D(str(r.remaining_quantity)),
            D(str(r.unit_cost)),
            r.cost_is_provisional,
            k(r.priced_by_line_id),
        )
        for r in (
            await db.execute(
                select(InventoryCostLayer).where(
                    InventoryCostLayer.warehouse_id == b.wh
                )
            )
        )
        .scalars()
        .all()
    )
    layer_source = {
        r.id: (k(r.source_line_id), r.layer_index)
        for r in (
            await db.execute(
                select(InventoryCostLayer).where(
                    InventoryCostLayer.warehouse_id == b.wh
                )
            )
        )
        .scalars()
        .all()
    }
    consumptions = _sorted(
        (
            k(r.consuming_line_id),
            layer_source.get(r.layer_id),
            D(str(r.quantity)),
            D(str(r.unit_cost)),
            D(str(r.total_cost)),
            r.is_shortfall,
            r.cost_is_provisional,
            k(r.priced_by_line_id),
        )
        for r in (
            await db.execute(
                select(InventoryCostLayerConsumption).where(
                    InventoryCostLayerConsumption.warehouse_id == b.wh
                )
            )
        )
        .scalars()
        .all()
    )
    levels = _sorted(
        (b.names[r.item_id], D(str(r.quantity)), D(str(r.average_cost)))
        for r in (
            await db.execute(
                select(InventoryLevel).where(InventoryLevel.warehouse_id == b.wh)
            )
        )
        .scalars()
        .all()
    )
    # The booked figures, frozen with the ledger line when it posts: never
    # restated by any replay, so they must be what the inline path booked.
    booked_rows = (
        await db.execute(
            select(InventoryTransactionItem, InventoryTransaction.total_cost)
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryTransactionItem.transaction_id,
            )
            .where(InventoryTransaction.branch_id == b.branch.id)
        )
    ).all()
    booked = _sorted(
        (
            k(line.id),
            D(str(line.unit_cost or 0)),
            D(str(line.total_cost or 0)),
            D(str(line.previous_unit_cost or 0)),
            D(str(line.balance_after_quantity or 0)),
            D(str(line.balance_after_value or 0)),
            D(str(transaction_total or 0)),
        )
        for line, transaction_total in booked_rows
    )
    return {
        "line_costs": line_costs,
        "layers": layers,
        "consumptions": consumptions,
        "levels": levels,
        "booked": booked,
    }


async def _queued(db, warehouse_id) -> InventoryCostingSettle | None:
    return await db.get(InventoryCostingSettle, warehouse_id)


def _count_replays(monkeypatch) -> list:
    """Record every warehouse replay ``costing_service`` runs."""
    calls: list = []
    real = costing_service.replay

    async def spy(db, **kw):
        calls.append(kw.get("warehouse_ids"))
        return await real(db, **kw)

    monkeypatch.setattr(costing_service, "replay", spy)
    return calls


@pytest.mark.parametrize("settle_midway", [False, True])
async def test_deferred_postings_settle_to_the_inline_projection(
    env, monkeypatch, settle_midway
):
    ids, Session = env
    replays = _count_replays(monkeypatch)

    with costing_service.inline_replay():
        await _scenario(Session, ids.inline, ids.user, settle_midway=False)
    inline_replays = len(replays)
    # Every deferrable refusal replayed inside its request, as before.
    assert inline_replays >= 5

    replays.clear()
    await _scenario(Session, ids.deferred, ids.user, settle_midway=settle_midway)
    async with Session() as db:
        # Only the refusals whose deferral could change a booked figure still
        # replayed inside their request (plus the loop's mid-way settle).
        assert len(replays) < inline_replays
        queued = await _queued(db, ids.deferred.wh)
        assert queued is not None
        assert queued.reason
        assert await _queued(db, ids.inline.wh) is None
        # The deferred branch is genuinely provisional until it settles.
        assert await _snapshot(db, ids.deferred) != await _snapshot(db, ids.inline)

    async with Session() as db:
        settled = await costing_service.settle_marked(db, budget_seconds=600)
    assert [s.warehouse_id for s in settled] == [ids.deferred.wh]
    assert settled[0].waited_ms >= 0

    async with Session() as db:
        assert await _queued(db, ids.deferred.wh) is None
        # Marked for the estate cascade exactly as an inline replay marks it.
        assert await db.get(InventoryCostingDirty, ids.deferred.wh) is not None
        inline = await _snapshot(db, ids.inline)
        deferred = await _snapshot(db, ids.deferred)
    assert inline["line_costs"], "the scenario posted nothing"
    for part in ("line_costs", "layers", "consumptions", "levels", "booked"):
        assert deferred[part] == inline[part], part


async def test_produce_all_replays_nothing_inline_and_settles_once(env, monkeypatch):
    ids, Session = env
    b = ids.deferred
    async with Session() as db:
        await seed_stock(
            db,
            branch_id=b.branch.id,
            warehouse_id=b.wh,
            item_id=b.items["Flour"],
            quantity="100",
            unit_cost="0.01",
        )
        await seed_stock(
            db,
            branch_id=b.branch.id,
            warehouse_id=b.wh,
            item_id=b.items["Butter"],
            quantity="100",
            unit_cost="0.04",
        )
        # Sold before they were made: each batch lands on negative stock.
        await _post(db, b, ids.user, T.CONSUMPTION_FROM_ORDERS, "Brownie", "4")
        await _post(db, b, ids.user, T.CONSUMPTION_FROM_ORDERS, "Cookie", "4")
        order = await transfer_service.create_production_order(
            db,
            source_branch=b.branch,
            user=ids.user,
            production_items=[
                SimpleNamespace(
                    item_id=b.items["Brownie"], quantity=D("6"), unit="storage"
                ),
                SimpleNamespace(
                    item_id=b.items["Cookie"], quantity=D("6"), unit="storage"
                ),
            ],
        )
        await db.commit()
        order_id = order.id

    replays = _count_replays(monkeypatch)
    async with Session() as db:
        await transfer_service.produce_all(db, order_id=order_id, user=ids.user)
        await db.commit()
    # Two batches, each deferred: no replay inside the request at all.
    assert replays == []

    async with Session() as db:
        settled = await costing_service.settle_marked(db, budget_seconds=600)
    # …and the settle loop replays the warehouse once for both.
    assert replays == [{b.wh}]
    assert [s.warehouse_id for s in settled] == [b.wh]


async def test_inline_mode_replayed_once_per_deferrable_posting(env, monkeypatch):
    """The before picture the coalescing removes: produce-all replayed per line."""
    ids, Session = env
    b = ids.inline
    async with Session() as db:
        await _post(db, b, ids.user, T.CONSUMPTION_FROM_ORDERS, "Brownie", "4")
        await _post(db, b, ids.user, T.CONSUMPTION_FROM_ORDERS, "Cookie", "4")
        order = await transfer_service.create_production_order(
            db,
            source_branch=b.branch,
            user=ids.user,
            production_items=[
                SimpleNamespace(
                    item_id=b.items["Brownie"], quantity=D("6"), unit="storage"
                ),
                SimpleNamespace(
                    item_id=b.items["Cookie"], quantity=D("6"), unit="storage"
                ),
            ],
        )
        await db.commit()
        order_id = order.id
    replays = _count_replays(monkeypatch)
    with costing_service.inline_replay():
        async with Session() as db:
            await transfer_service.produce_all(db, order_id=order_id, user=ids.user)
            await db.commit()
    assert len(replays) >= 2


async def _defer_one(Session, b, user) -> None:
    """One posting that defers: a delivery onto a shortfall."""
    async with Session() as db:
        await _post(db, b, user, T.CONSUMPTION_FROM_ORDERS, "Butter", "10")
        await _post(
            db, b, user, T.PURCHASING, "Butter", "50", "0.04", purchase_order_id=b.po
        )
        await db.commit()


async def test_a_failed_settle_keeps_the_warehouse_queued(env, monkeypatch):
    ids, Session = env
    b = ids.deferred
    await _defer_one(Session, b, ids.user)

    async def boom(db, **kw):
        raise RuntimeError("replay blew up")

    monkeypatch.setattr(costing_service, "replay", boom)
    async with Session() as db:
        assert await costing_service.settle_marked(db, budget_seconds=600) == []
    async with Session() as db:
        assert await _queued(db, b.wh) is not None

    monkeypatch.undo()
    # Retried next tick, and cleared once it succeeds.
    async with Session() as db:
        settled = await costing_service.settle_marked(db, budget_seconds=600)
    assert [s.warehouse_id for s in settled] == [b.wh]
    async with Session() as db:
        assert await _queued(db, b.wh) is None
        cost = await db.scalar(
            select(func.max(InventoryLineCost.unit_cost)).where(
                InventoryLineCost.warehouse_id == b.wh,
                InventoryLineCost.quantity < 0,
            )
        )
    # The sale that ran ahead of the delivery took the delivery's price.
    assert D(str(cost)) == D("0.04")


async def test_a_busy_branch_is_skipped_never_waited_on(env, engine):
    ids, Session = env
    b = ids.deferred
    await _defer_one(Session, b, ids.user)
    async with Session() as holder:
        # A till mid-posting at the branch holds its inventory lock.
        await holder.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"inventory:{b.branch.id}", 0)
                )
            )
        )
        async with Session() as db:
            assert await costing_service.settle_marked(db, budget_seconds=600) == []
        async with Session() as db:
            assert await _queued(db, b.wh) is not None
        await holder.rollback()
    async with Session() as db:
        settled = await costing_service.settle_marked(db, budget_seconds=600)
    assert [s.warehouse_id for s in settled] == [b.wh]


async def test_the_estate_sweep_clears_the_settle_queue(env):
    """The backstop when the settle loop is down."""
    ids, Session = env
    b = ids.deferred
    await _defer_one(Session, b, ids.user)
    async with Session() as db:
        assert await _queued(db, b.wh) is not None
        await costing_service.replay_estate(db, wait=True)
        await db.commit()
    async with Session() as db:
        assert await _queued(db, b.wh) is None


async def test_an_inline_replay_settles_what_was_deferred_before_it(env):
    """A refusal that cannot defer (a void) replays the warehouse inline, which
    covers every earlier deferral there — so the queue entry goes with it."""
    from app.services.inventory import ledger_service

    ids, Session = env
    b = ids.deferred
    await _defer_one(Session, b, ids.user)
    async with Session() as db:
        receipt = await _post(
            db, b, ids.user, T.PURCHASING, "Flour", "10", "0.02", purchase_order_id=b.po
        )
        await ledger_service.reverse_transaction(
            db, transaction_id=receipt.id, user=ids.user, reason="keyed twice"
        )
        await db.commit()
    async with Session() as db:
        assert await _queued(db, b.wh) is None
