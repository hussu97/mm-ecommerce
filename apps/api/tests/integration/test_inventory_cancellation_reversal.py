"""Pre-packing cancellations reverse their consumption; post-packing ones don't.

Consumption is posted when MM accepts the order (`accept_order`), long before the
goods are boxed. If the order is cancelled while still pre-packing the goods were
never actually made, so `record_order_cancellation(pre_packing=True)` reverses the
sale movement in full (a RETURN_FROM_ORDERS referencing the same order) and it
stops counting as Sold. Once packed the goods are assumed consumed, so the
cancellation leaves the consumption and logs a disposition exception instead.
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
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
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
from app.services.inventory.recipe_service import RecipeLineInput

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-cancel-reversal"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _seed_order(db) -> dict:
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
    ingredient = InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        name="Sugar",
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="g",
        ingredient_unit="g",
        storage_to_ingredient_factor=Decimal("1"),
    )
    db.add(ingredient)
    product = Product(name="Cake", slug=f"cake-{uuid.uuid4().hex[:8]}")
    db.add(product)
    await db.flush()
    await recipe_service.draft_and_activate(
        db,
        kind="product",
        owner_id=product.id,
        lines=[RecipeLineInput(item_id=ingredient.id, quantity=Decimal("2"))],
        user_id=user.id,
    )
    order = Order(
        order_number=f"CR-{uuid.uuid4().hex[:10]}",
        email="pytest-cr@example.com",
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
            product_sku="CAKE",
            quantity=1,
            base_price=Decimal("10"),
            unit_price=Decimal("10"),
            total_price=Decimal("10"),
        )
    )
    await db.commit()
    return {
        "branch_id": branch.id,
        "order_id": order.id,
        "ingredient_id": ingredient.id,
        "user_id": user.id,
    }


async def _level_qty(db, item_id) -> Decimal:
    level = (
        await db.execute(
            select(InventoryLevel).where(InventoryLevel.item_id == item_id)
        )
    ).scalar_one()
    return Decimal(str(level.quantity))


async def _returns(db, order_id) -> list[InventoryTransaction]:
    return list(
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.order_id == order_id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                )
            )
        )
        .scalars()
        .all()
    )


async def _teardown(engine, ids) -> None:
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        bid = ids["branch_id"]
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
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id == ids["ingredient_id"]
            )
        )
        await db.execute(
            OrderItem.__table__.delete().where(OrderItem.order_id == ids["order_id"])
        )
        await db.execute(Order.__table__.delete().where(Order.id == ids["order_id"]))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def test_pre_packing_cancellation_reverses_the_sale(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        ids = await _seed_order(db)
    try:
        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            user = await db.get(User, ids["user_id"])
            event = await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()
            assert event.status == InventorySourceEventStatusEnum.POSTED.value
        async with Session() as db:
            # One cake -> 2g sugar consumed.
            assert await _level_qty(db, ids["ingredient_id"]) == Decimal("-2")

        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            await source_event_service.record_order_cancellation(
                db, order, pre_packing=True
            )
            await db.commit()

        async with Session() as db:
            # The consumption is reversed in full: a RETURN_FROM_ORDERS lands and
            # the level is back to zero — nothing left counting as Sold.
            returns = await _returns(db, ids["order_id"])
            assert len(returns) == 1
            assert await _level_qty(db, ids["ingredient_id"]) == Decimal("0")
            # No disposition exception was logged — the reversal is automatic.
            exception = await db.scalar(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key
                    == f"order-cancel:{ids['order_id']}:1"
                )
            )
            assert exception is None
    finally:
        await _teardown(engine, ids)


async def test_pre_packing_cancellation_does_not_double_reverse_a_voided_order(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        ids = await _seed_order(db)
    try:
        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            user = await db.get(User, ids["user_id"])
            await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()
        # A counter void reverses it first.
        async with Session() as db:
            from app.services.inventory import inventory_service

            order = await db.get(Order, ids["order_id"])
            user = await db.get(User, ids["user_id"])
            await inventory_service.restock_for_void(db, order=order, user=user)
            await db.commit()
        # The cancellation must not try to reverse again (the FIFO cap would raise).
        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            await source_event_service.record_order_cancellation(
                db, order, pre_packing=True
            )
            await db.commit()
        async with Session() as db:
            assert len(await _returns(db, ids["order_id"])) == 1
            assert await _level_qty(db, ids["ingredient_id"]) == Decimal("0")
    finally:
        await _teardown(engine, ids)


async def test_post_packing_cancellation_leaves_the_goods_consumed(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        ids = await _seed_order(db)
    try:
        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            user = await db.get(User, ids["user_id"])
            await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()

        async with Session() as db:
            order = await db.get(Order, ids["order_id"])
            await source_event_service.record_order_cancellation(
                db, order, pre_packing=False
            )
            await db.commit()

        async with Session() as db:
            # Nothing reversed: the goods are assumed consumed, so the level stays
            # down and a disposition exception is logged for a person to resolve.
            assert await _returns(db, ids["order_id"]) == []
            assert await _level_qty(db, ids["ingredient_id"]) == Decimal("-2")
            exception = await db.scalar(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key
                    == f"order-cancel:{ids['order_id']}:1"
                )
            )
            assert exception is not None
            assert exception.status == InventorySourceEventStatusEnum.EXCEPTION.value
            assert exception.error_code == "return_disposition_required"
    finally:
        await _teardown(engine, ids)
