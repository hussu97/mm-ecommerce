"""One poison event must not wedge a branch's whole sweep (F-INV-4).

The source-event sweeper processes a branch's pending events in one transaction.
Before this, an event that raised an unexpected error (a corrupt source_id, a
bad frozen plan) aborted that transaction, so every other pending event in the
branch rolled back and replayed — and re-poisoned — on every tick. Per-event
isolation quarantines the poison and lets the rest post.
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
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventorySourceEvent,
    InventorySourceEventStatusEnum,
)
from app.models.order import DeliveryMethodEnum, Order
from app.services.inventory import source_event_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-sweep-poison"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def test_a_poison_event_is_quarantined_and_the_rest_of_the_branch_posts(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    created: dict = {}
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
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
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Sugar",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("1"),
        )
        db.add(item)
        order = Order(
            order_number=f"SP-{uuid.uuid4().hex[:10]}",
            email="pytest-poison@example.com",
            source="online",
            branch_id=branch.id,
            business_date=BUSINESS_DATE,
            delivery_method=DeliveryMethodEnum.PICKUP,
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
        )
        db.add(order)
        await db.flush()

        now = source_event_service.utcnow()
        # Poison FIRST (lower accepted_sequence): a source_id that is not a UUID
        # raises inside the sweep before any handled path can catch it.
        poison = InventorySourceEvent(
            branch_id=branch.id,
            source_type="order",
            source_id="not-a-uuid",
            source_revision=1,
            idempotency_key=f"{MARKER}-poison-{uuid.uuid4().hex[:8]}",
            status=InventorySourceEventStatusEnum.PENDING.value,
            occurred_at=now,
            accepted_at=now,
            frozen_plan={},
            recipe_version_ids=[],
        )
        db.add(poison)
        await db.flush()
        # Healthy SECOND: a valid frozen plan the poster can post as-is.
        healthy = InventorySourceEvent(
            branch_id=branch.id,
            source_type="order",
            source_id=str(order.id),
            source_revision=1,
            idempotency_key=f"{MARKER}-healthy-{uuid.uuid4().hex[:8]}",
            status=InventorySourceEventStatusEnum.PENDING.value,
            occurred_at=now,
            accepted_at=now,
            frozen_plan={
                "lines": [
                    {
                        "item_id": str(item.id),
                        "quantity": "2",
                        "recipe_version_ids": [],
                        "paths": [],
                    }
                ]
            },
            recipe_version_ids=[],
        )
        db.add(healthy)
        await db.commit()
        created = {
            "branch_id": branch.id,
            "order_id": order.id,
            "item_id": item.id,
            "poison_id": poison.id,
            "healthy_id": healthy.id,
        }

    try:
        # The sweep must not raise, must quarantine the poison, and must post the
        # healthy event even though the poison is first in acceptance order.
        processed = await source_event_service.sweep_pending_once()
        assert processed >= 2

        async with Session() as db:
            poison = await db.get(InventorySourceEvent, created["poison_id"])
            healthy = await db.get(InventorySourceEvent, created["healthy_id"])
            assert poison.status == InventorySourceEventStatusEnum.EXCEPTION.value
            assert poison.error_code == "sweep_failed"
            assert healthy.status == InventorySourceEventStatusEnum.POSTED.value
            assert healthy.transaction_id is not None

            level = (
                await db.execute(
                    select(InventoryLevel).where(
                        InventoryLevel.item_id == created["item_id"]
                    )
                )
            ).scalar_one()
            assert Decimal(str(level.quantity)) == Decimal("-2"), "healthy line posted"
    finally:
        async with Session() as db:
            await db.execute(text("SET session_replication_role = 'replica'"))
            bid = created["branch_id"]
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
                InventorySourceEvent.__table__.delete().where(
                    InventorySourceEvent.branch_id == bid
                )
            )
            await db.execute(
                Order.__table__.delete().where(Order.id == created["order_id"])
            )
            await db.execute(
                InventoryLevel.__table__.delete().where(
                    InventoryLevel.item_id == created["item_id"]
                )
            )
            await db.execute(
                BranchInventorySettings.__table__.delete().where(
                    BranchInventorySettings.branch_id == bid
                )
            )
            await db.execute(
                InventoryItem.__table__.delete().where(
                    InventoryItem.id == created["item_id"]
                )
            )
            await db.execute(
                Warehouse.__table__.delete().where(Warehouse.branch_id == bid)
            )
            await db.execute(Branch.__table__.delete().where(Branch.id == bid))
            await db.execute(text("SET session_replication_role = 'origin'"))
            await db.commit()
