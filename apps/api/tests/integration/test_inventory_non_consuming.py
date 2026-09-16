"""A `consumes_stock=False` product draws no tracked inventory and closes clean.

Inventory-v2 expects every sold product to carry a recipe; a line for one without
an active recipe raises `missing_recipe` and its source event sits PENDING forever
(re-snapshot every sweep), which is wrong for a product that legitimately consumes
nothing (a made-to-order beverage, an item whose consumption lives on its modifier
options). `consumes_stock=False` makes `snapshot_order` skip the product's recipe
expansion (no warning) and lets the event CLOSE as a clean no-movement — for a
fresh order (`accept_order`) and, once the flag is set, for a stuck PENDING one on
the next sweep (`retry_event`).
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
from app.services.inventory import source_event_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-nonconsuming"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _make_order(db, *, consumes_stock: bool):
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
    # No recipe is ever created for this product — the whole point.
    product = Product(
        name=f"{MARKER} Cappuccino",
        slug=f"{MARKER}-{uuid.uuid4().hex[:10]}",
        consumes_stock=consumes_stock,
    )
    db.add(product)
    await db.flush()
    order = Order(
        order_number=f"NC-{uuid.uuid4().hex[:10]}",
        email="pytest-nc@example.com",
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
            product_name="Cappuccino",
            product_sku="CAP",
            quantity=1,
            base_price=Decimal("10"),
            unit_price=Decimal("10"),
            total_price=Decimal("10"),
        )
    )
    await db.flush()
    return branch.id, order.id, user.id, product.id


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


async def test_non_consuming_product_closes_with_no_movement(engine):
    """`consumes_stock=False` + no recipe → the event POSTS (closes) with no
    transaction and no missing_recipe warning; nothing is left PENDING."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch_id, order_id, user_id, _ = await _make_order(db, consumes_stock=False)
        await db.commit()
    try:
        async with Session() as db:
            order = await db.get(Order, order_id)
            user = await db.get(User, user_id)
            event = await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()
            assert event.status == InventorySourceEventStatusEnum.POSTED.value
            assert event.error_code is None
            assert event.transaction_id is None  # a clean no-movement close
    finally:
        await _cleanup(Session, branch_id)


async def test_consuming_product_without_recipe_stays_pending(engine):
    """The default `consumes_stock=True` + no recipe still raises missing_recipe and
    stays PENDING — a genuine gap must not be silently closed."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch_id, order_id, user_id, product_id = await _make_order(
            db, consumes_stock=True
        )
        await db.commit()
    try:
        async with Session() as db:
            order = await db.get(Order, order_id)
            user = await db.get(User, user_id)
            event = await source_event_service.accept_order(db, order=order, user=user)
            await db.commit()
            assert event.status == InventorySourceEventStatusEnum.PENDING.value
            assert event.error_code == "missing_recipe"

        # Now flip the product to non-consuming; the sweeper's retry closes it.
        async with Session() as db:
            product = await db.get(Product, product_id)
            product.consumes_stock = False
            await db.flush()
            event = (
                await db.execute(
                    select(InventorySourceEvent).where(
                        InventorySourceEvent.source_id == str(order_id)
                    )
                )
            ).scalar_one()
            order = await db.get(Order, order_id)
            closed = await source_event_service.retry_event(
                db, event=event, order=order, user=None
            )
            await db.commit()
            assert closed is None  # no movement transaction
            refreshed = await db.get(InventorySourceEvent, event.id)
            assert refreshed.status == InventorySourceEventStatusEnum.POSTED.value
            assert refreshed.error_code is None
    finally:
        await _cleanup(Session, branch_id)
