"""Voiding the last live line of an open counter check voids the whole check.

`void_item` used to void the line and stop there, so voiding the only item on a
check left it at `created`/`active` with a zero total — a check that owes nothing
yet is still open. That limbo showed up on the admin order as a live order whose
every line was void. Voiding the last line now cascades to a check void: the
order becomes `void` + `cancelled`, exactly as if the cashier had voided the
whole check. It stays a line-only void when the check still holds money, so the
explicit void flow (which refunds first) is the only path that touches a payment.

Against a real Postgres because the cascade runs the order through the lifecycle
transition, which a mocked session cannot answer.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderItem, OrderStatusEnum
from app.models.payment_method import PaymentMethod
from app.models.product import Product
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.services.pos import pos_order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-void-cascade"
BUSINESS_DATE = "2026-09-08"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _open_check(db, *, prices: list[Decimal]) -> dict:
    """An open cashier check at a fresh branch with one line per price."""
    branch = Branch(name=f"{MARKER} b", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}")
    db.add(branch)
    await db.flush()
    db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
    cashier = User(email=f"{MARKER}-{uuid.uuid4().hex[:8]}@ex.com", hashed_password="x")
    method = PaymentMethod(
        name="Cash", code=f"cash-{uuid.uuid4().hex[:8]}", type="cash"
    )
    product = Product(name="Brownie", slug=f"brownie-{uuid.uuid4().hex[:8]}")
    db.add_all([cashier, method, product])
    await db.flush()
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
    total = sum(prices, Decimal("0"))
    order = Order(
        order_number=f"VI-{uuid.uuid4().hex[:12]}",
        email="pytest-void-cascade@example.com",
        source="cashier",
        is_pos=True,
        pos_status="active",
        business_date=BUSINESS_DATE,
        branch_id=branch.id,
        status=OrderStatusEnum.CREATED,
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=total,
        total=total,
    )
    db.add(order)
    await db.flush()
    item_ids = []
    for price in prices:
        item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name="Brownie",
            product_sku="BROWNIE",
            quantity=1,
            base_price=price,
            unit_price=price,
            total_price=price,
        )
        db.add(item)
        await db.flush()
        item_ids.append(item.id)
    await db.commit()
    return {
        "order_id": order.id,
        "user_id": cashier.id,
        "method_id": method.id,
        "till_id": till.id,
        "item_ids": item_ids,
    }


async def test_voiding_the_last_line_voids_the_whole_check(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _open_check(db, prices=[Decimal("35.00")])

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        user = await db.get(User, env["user_id"])
        result = await pos_order_service.void_item(
            db, order=order, item_id=env["item_ids"][0], user=user
        )
        await db.commit()
        assert result.pos_status == "void"
        assert result.status == OrderStatusEnum.CANCELLED

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        assert order.pos_status == "void"
        assert order.status == OrderStatusEnum.CANCELLED
        assert order.voided_at is not None
        assert all(i.status == "void" for i in order.items)
        # The emptied check owes nothing.
        assert Decimal(str(order.total)) == Decimal("0.00")


async def test_voiding_one_of_two_lines_leaves_the_check_open(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _open_check(db, prices=[Decimal("35.00"), Decimal("15.00")])

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        user = await db.get(User, env["user_id"])
        result = await pos_order_service.void_item(
            db, order=order, item_id=env["item_ids"][0], user=user
        )
        await db.commit()
        # A live line remains: the check stays open and still has value (the exact
        # figure carries the standing cashier auto-discount, so assert it is > 0
        # rather than the bare line price).
        assert result.pos_status == "active"
        assert result.status == OrderStatusEnum.CREATED
        assert Decimal(str(result.total)) > 0

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        # One line voided, one still live (a counter line not yet sent carries a
        # null status, which the cascade guard treats as live — so no cascade).
        voided = [i for i in order.items if i.status == "void"]
        live = [i for i in order.items if i.status != "void"]
        assert len(voided) == 1 and len(live) == 1


async def test_a_paid_check_does_not_cascade_on_the_last_void(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        env = await _open_check(db, prices=[Decimal("35.00")])

    # Money is on the check: voiding the last line must not silently cancel it —
    # that would strand a held payment against a cancelled sale. The line voids,
    # the check stays open for the explicit void flow to refund and end.
    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        user = await db.get(User, env["user_id"])
        till = await db.get(Till, env["till_id"])
        await pos_order_service.record_payment(
            db,
            order=order,
            user=user,
            payment_method_id=env["method_id"],
            amount=Decimal("35.00"),
            till=till,
        )
        await db.commit()

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        user = await db.get(User, env["user_id"])
        result = await pos_order_service.void_item(
            db, order=order, item_id=env["item_ids"][0], user=user
        )
        await db.commit()
        assert result.pos_status == "active"
        assert result.status == OrderStatusEnum.CREATED

    async with Session() as db:
        order = await pos_order_service.get_order(db, env["order_id"])
        assert order.pos_status == "active"
        assert order.items[0].status == "void"
        assert pos_order_service._net_paid(order) == Decimal("35.00")
