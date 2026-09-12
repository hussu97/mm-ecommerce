"""Voiding a completed counter sale refunds it, restocks it, and cancels it.

A cashier sale can be voided after it is done — the customer changes their mind
once the money is taken and the box is collected. The register used to refuse
this (`void_order` asserted the check was still open and that no money was
held), so the only recourse was the admin console. `void_order` now recognises
a CLOSED cashier check as a post-sale void and:

* refunds everything still held, tender by tender, back to the drawer;
* reverses the recipe inventory the sale consumed at close (full restock);
* cancels the order through the lifecycle's admin `extra_from` hatch, since a
  collected counter sale sits at `delivered`.

This exercises the whole thing against a real Postgres — consumption is posted
by a genuine close and has to come back — which a mocked session cannot show.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.money import money
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import InventoryItem, InventoryLevel, Warehouse
from app.models.inventory_v2 import BranchInventorySettings
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.payment_method import PaymentMethod
from app.models.product import Product
from app.models.till import (
    DrawerOperation,
    DrawerOperationTypeEnum,
    Till,
    TillStatusEnum,
)
from app.models.user import User
from app.services.inventory import recipe_service
from app.services.inventory.recipe_service import RecipeLineInput
from app.services.pos import pos_order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-void-completed"
BUSINESS_DATE = "2026-09-08"


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


async def test_voiding_a_completed_counter_sale_refunds_restocks_and_cancels(engine):
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
                go_live_at=utcnow(),
                go_live_sequence=0,
            )
        )
        cashier = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@ex.com", hashed_password="x"
        )
        method = PaymentMethod(
            name="Cash", code=f"cash-{uuid.uuid4().hex[:8]}", type="cash"
        )
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
        product = Product(name="Brownie", slug=f"brownie-{uuid.uuid4().hex[:8]}")
        db.add_all([cashier, method, ingredient, product])
        await db.flush()
        # One brownie consumes 2g of sugar.
        await recipe_service.draft_and_activate(
            db,
            kind="product",
            owner_id=product.id,
            lines=[RecipeLineInput(item_id=ingredient.id, quantity=Decimal("2"))],
            user_id=cashier.id,
        )

        till = Till(
            branch_id=branch.id,
            user_id=cashier.id,
            business_date=BUSINESS_DATE,
            status=TillStatusEnum.OPEN.value,
            opening_amount=Decimal("200.00"),
            estimated_cash=Decimal("200.00"),
            variance=Decimal("0.00"),
            opened_at=utcnow(),
        )
        db.add(till)

        order = Order(
            order_number=f"VC-{uuid.uuid4().hex[:12]}",
            email="pytest-void@example.com",
            source="cashier",
            is_pos=True,
            pos_status="active",
            business_date=BUSINESS_DATE,
            branch_id=branch.id,
            status=OrderStatusEnum.CREATED,
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
                product_name="Brownie",
                product_sku="BROWNIE",
                quantity=1,
                base_price=Decimal("10"),
                unit_price=Decimal("10"),
                total_price=Decimal("10"),
            )
        )
        await db.commit()
        created = {
            "order_id": order.id,
            "user_id": cashier.id,
            "method_id": method.id,
            "till_id": till.id,
            "ingredient_id": ingredient.id,
        }

    # Pay it in cash, then close it — the close posts the recipe consumption.
    async with Session() as db:
        order = await pos_order_service.get_order(db, created["order_id"])
        user = await db.get(User, created["user_id"])
        till = await db.get(Till, created["till_id"])
        await pos_order_service.record_payment(
            db,
            order=order,
            user=user,
            payment_method_id=created["method_id"],
            amount=Decimal("10.00"),
            till=till,
        )
        await pos_order_service.close_order(db, order=order, user=user)
        await db.commit()

    async with Session() as db:
        order = await pos_order_service.get_order(db, created["order_id"])
        assert order.pos_status == "closed"
        assert order.status == OrderStatusEnum.DELIVERED
        assert pos_order_service._net_paid(order) == Decimal("10.00")
        # The sale consumed 2g: the level dropped by two (negative stock allowed).
        assert await _level_qty(db, created["ingredient_id"]) == Decimal("-2")

    # Void the completed sale.
    async with Session() as db:
        order = await pos_order_service.get_order(db, created["order_id"])
        user = await db.get(User, created["user_id"])
        result = await pos_order_service.void_order(db, order=order, user=user)
        await db.commit()
        assert result.pos_status == "void"
        assert result.status == OrderStatusEnum.CANCELLED

    async with Session() as db:
        order = await pos_order_service.get_order(db, created["order_id"])
        # Money handed back in full: the refund nets the check to zero.
        assert pos_order_service._net_paid(order) == Decimal("0.00")
        refunds = [p for p in order.payments if p.is_refund]
        assert len(refunds) == 1
        assert money(refunds[0].amount) == Decimal("10.00")
        # Cash returned to the drawer as a RETURN operation.
        returns = (
            (
                await db.execute(
                    select(DrawerOperation).where(
                        DrawerOperation.till_id == created["till_id"],
                        DrawerOperation.type == DrawerOperationTypeEnum.RETURN.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(returns) == 1
        # The consumed ingredient is back on the shelf: -2 consumed, +2 restocked.
        assert await _level_qty(db, created["ingredient_id"]) == Decimal("0")
