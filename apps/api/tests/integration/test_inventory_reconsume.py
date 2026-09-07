"""Sales consumption re-runs after a post-close edit (F-INV-10).

The source event's idempotency key used to be hardcoded to revision 1, so a
billable-line edit after acceptance never re-consumed — the movement kept
matching the original bill. The revision counter plus reconsume_order reverses
the prior movement and posts a fresh one for the new revision, in one
correction_group_id, and is idempotent on retry.
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
    Recipe,
    RecipeVersion,
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

MARKER = "pytest-reconsume"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _level_qty(db, item_id) -> Decimal:
    level = (
        await db.execute(
            select(InventoryLevel).where(InventoryLevel.item_id == item_id)
        )
    ).scalar_one()
    return Decimal(str(level.quantity))


async def test_a_post_close_edit_reverses_and_reconsumes_in_one_group(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    created: dict = {}
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
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
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@ex.com", hashed_password="x"
        )
        db.add(user)
        ingredient = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name="Sugar",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
            cost=Decimal("1"),
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
            order_number=f"RC-{uuid.uuid4().hex[:10]}",
            email="pytest-rc@example.com",
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
        created = {
            "branch_id": branch.id,
            "order_id": order.id,
            "ingredient_id": ingredient.id,
            "user_id": user.id,
            "product_id": product.id,
        }

    try:
        # First consumption: one cake -> 2g sugar. Level goes to -2 (negative ok).
        async with Session() as db:
            order = await db.get(Order, created["order_id"])
            user = await db.get(User, created["user_id"])
            event = await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()
            assert event.status == InventorySourceEventStatusEnum.POSTED.value
            assert event.source_revision == 1
        async with Session() as db:
            assert await _level_qty(db, created["ingredient_id"]) == Decimal("-2")

        # The bill is edited after close: the cake becomes two cakes (-> 4g).
        async with Session() as db:
            order = await db.get(Order, created["order_id"])
            user = await db.get(User, created["user_id"])
            item = (
                await db.execute(
                    select(OrderItem).where(OrderItem.order_id == order.id)
                )
            ).scalar_one()
            item.quantity = 2
            source_event_service.bump_inventory_revision(order)
            await db.flush()
            new_event = await source_event_service.reconsume_order(
                db, order=order, user=user
            )
            await db.commit()
            assert new_event.source_revision == 2
            assert new_event.status == InventorySourceEventStatusEnum.POSTED.value

        async with Session() as db:
            # Net stock: -2 (rev1) +2 (reversal) -4 (rev2) = -4.
            assert await _level_qty(db, created["ingredient_id"]) == Decimal("-4")

            # The reversal and the fresh consumption share one correction group.
            rev2_event = await db.get(InventorySourceEvent, new_event.id)
            rev2_txn = await db.get(InventoryTransaction, rev2_event.transaction_id)
            assert rev2_txn.correction_group_id is not None
            reversal = (
                await db.execute(
                    select(InventoryTransaction).where(
                        InventoryTransaction.reverses_transaction_id.isnot(None),
                        InventoryTransaction.correction_group_id
                        == rev2_txn.correction_group_id,
                    )
                )
            ).scalar_one()
            assert reversal.reverses_transaction_id is not None

        # A retry of the re-consumption moves nothing further.
        async with Session() as db:
            order = await db.get(Order, created["order_id"])
            user = await db.get(User, created["user_id"])
            again = await source_event_service.reconsume_order(
                db, order=order, user=user
            )
            await db.commit()
            assert again.id == new_event.id
        async with Session() as db:
            assert await _level_qty(db, created["ingredient_id"]) == Decimal("-4")
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
                OrderItem.__table__.delete().where(
                    OrderItem.order_id == created["order_id"]
                )
            )
            await db.execute(
                Order.__table__.delete().where(Order.id == created["order_id"])
            )
            await db.execute(
                RecipeVersion.__table__.delete().where(
                    RecipeVersion.recipe_id.in_(
                        select(Recipe.id).where(
                            Recipe.product_id == created["product_id"]
                        )
                    )
                )
            )
            await db.execute(
                Recipe.__table__.delete().where(
                    Recipe.product_id == created["product_id"]
                )
            )
            await db.execute(
                InventoryLevel.__table__.delete().where(
                    InventoryLevel.item_id == created["ingredient_id"]
                )
            )
            await db.execute(
                BranchInventorySettings.__table__.delete().where(
                    BranchInventorySettings.branch_id == bid
                )
            )
            await db.execute(
                InventoryItem.__table__.delete().where(
                    InventoryItem.id == created["ingredient_id"]
                )
            )
            await db.execute(
                Product.__table__.delete().where(Product.id == created["product_id"])
            )
            await db.execute(
                Warehouse.__table__.delete().where(Warehouse.branch_id == bid)
            )
            await db.execute(
                User.__table__.delete().where(User.id == created["user_id"])
            )
            await db.execute(Branch.__table__.delete().where(Branch.id == bid))
            await db.execute(text("SET session_replication_role = 'origin'"))
            await db.commit()
