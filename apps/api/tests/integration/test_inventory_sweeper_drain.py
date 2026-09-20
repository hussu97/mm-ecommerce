"""The pending sweeper drains a branch backlog within its budget (F-INV).

Two guards against the branch-backlog stall: the sweep must (1) load the active
recipe graph ONCE per branch batch, not once per event — the per-event reload is
what pushed a backlog past the budget and rolled the whole branch back every tick,
draining nothing forever — and (2) cap how many events one branch drains per tick,
so an unbounded backlog still commits inside the budget and continues next tick.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryTransaction,
    InventoryTransactionItem,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventorySourceEvent,
    InventorySourceEventStatusEnum,
)
from app.models.order import DeliveryMethodEnum, Order, OrderItem
from app.models.product import Product
from app.models.user import User
from app.services.inventory import recipe_service, source_event_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-sweepdrain"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _seed(db, *, n_orders: int):
    """A branch with inventory on and `n_orders` orders for one consuming product
    that has NO recipe — each accepts to a PENDING missing_recipe event."""
    branch = Branch(name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}")
    db.add(branch)
    await db.flush()
    db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
    db.add(
        BranchInventorySettings(
            branch_id=branch.id,
            inventory_enabled=True,
            sales_consumption_enabled=True,
            allow_negative_stock=True,
            go_live_at=source_event_service.utcnow(),
            go_live_sequence=0,
        )
    )
    user = User(email=f"{MARKER}-{uuid.uuid4().hex[:8]}@ex.com", hashed_password="x")
    db.add(user)
    product = Product(  # consuming, but no recipe → stays PENDING missing_recipe
        name=f"{MARKER} Cake",
        slug=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        consumes_stock=True,
    )
    db.add(product)
    await db.flush()
    for _ in range(n_orders):
        order = Order(
            order_number=f"SD-{uuid.uuid4().hex[:10]}",
            email="pytest-sd@example.com",
            source="online",
            branch_id=branch.id,
            business_date=BUSINESS_DATE,
            delivery_method=DeliveryMethodEnum.PICKUP,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        db.add(order)
        await db.flush()
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name="Cake",
                product_sku="CK",
                quantity=1,
                base_price=Decimal("10"),
                unit_price=Decimal("10"),
                total_price=Decimal("10"),
            )
        )
        await db.flush()
        ev = await source_event_service.accept_order(db, order=order, user=user)
        assert ev.status == InventorySourceEventStatusEnum.PENDING.value
    return branch.id, product.id


async def _cleanup(Session, branch_id):
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
        for tbl in (
            InventoryTransaction,
            InventorySourceEvent,
            BranchInventorySettings,
            Warehouse,
        ):
            await db.execute(tbl.__table__.delete().where(tbl.branch_id == branch_id))
        await db.execute(
            OrderItem.__table__.delete().where(
                OrderItem.order_id.in_(
                    select(Order.id).where(Order.branch_id == branch_id)
                )
            )
        )
        await db.execute(Order.__table__.delete().where(Order.branch_id == branch_id))
        await db.execute(
            Product.__table__.delete().where(Product.slug.like(f"{MARKER}-%"))
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def test_branch_sweep_loads_the_catalog_once_for_the_whole_batch(
    engine, monkeypatch
):
    """Re-snapshotting five pending events reloads the recipe graph ONCE, not five
    times."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch_id, _ = await _seed(db, n_orders=5)
        # A newly accepted missing-recipe event has already tried the current
        # catalog and must sleep.  Advancing the durable graph generation models
        # the recipe activation that makes the backlog eligible for one retry.
        await recipe_service._bump_catalog_generation(db)
        await db.commit()
    try:
        calls = {"n": 0}
        real = recipe_service.load_active_catalog

        async def _counting(db):
            calls["n"] += 1
            return await real(db)

        monkeypatch.setattr(recipe_service, "load_active_catalog", _counting)
        async with Session() as db:
            processed = await source_event_service._sweep_branch_pending(db, branch_id)
            await db.commit()
        assert processed == 5
        assert calls["n"] == 1  # one load reused across all five events, not five
    finally:
        await _cleanup(Session, branch_id)


async def test_branch_sweep_caps_the_batch_and_continues_next_tick(engine, monkeypatch):
    """With a batch cap of 2 and three pending events, one sweep drains 2; the
    third is left for the next tick (the cursor re-selects it)."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch_id, product_id = await _seed(db, n_orders=3)
        # Flip the product non-consuming so a swept event CLOSES and leaves the
        # pending set (otherwise a still-missing recipe keeps every event pending
        # and each capped sweep re-processes the same batch).
        product = await db.get(Product, product_id)
        product.consumes_stock = False
        # The source events were stamped with the generation they tried during
        # acceptance.  Model a later recipe-graph change so this recovery pass is
        # eligible; the batch cap should still leave the third row for next tick.
        await recipe_service._bump_catalog_generation(db)
        await db.commit()
    try:
        monkeypatch.setattr(source_event_service, "_SWEEP_BRANCH_BATCH", 2)
        async with Session() as db:
            processed = await source_event_service._sweep_branch_pending(db, branch_id)
            await db.commit()
        assert processed == 2  # capped, not all three

        # The third, left for the next tick, now drains.
        async with Session() as db:
            processed = await source_event_service._sweep_branch_pending(db, branch_id)
            await db.commit()
        assert processed == 1

        async with Session() as db:
            still_pending = await db.scalar(
                select(InventorySourceEvent)
                .where(
                    InventorySourceEvent.branch_id == branch_id,
                    InventorySourceEvent.status
                    == InventorySourceEventStatusEnum.PENDING.value,
                )
                .limit(1)
            )
            assert still_pending is None  # the whole backlog drained over two ticks
    finally:
        await _cleanup(Session, branch_id)
