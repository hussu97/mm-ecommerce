"""
Two terminals paying one check at once cannot overpay it (F-POS-6).

`record_payment` read the outstanding balance off the order's own payment
collection and took no row lock, so two terminals each reading an unpaid check
both took the full amount and the shop was overpaid. The fix locks the order row
(`SELECT … FOR UPDATE`) and re-reads through `get_order`, so the second caller
waits, sees the first payment, and its `amount > outstanding` guard turns it back.

Row locks across two connections need a real Postgres; two sessions under
`asyncio.gather` stand in for two terminals pressing pay together.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models import Branch, User
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.payment_method import PaymentMethod
from app.models.pos_order import OrderPayment
from app.services.pos import pos_order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-pay-concurrency"
BUSINESS_DATE = "2026-09-07"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    """An open counter check for 100.00, a cashier, and a card payment method."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        user = User(email=f"pay-{uuid.uuid4().hex[:10]}@example.com", is_staff=True)
        method = PaymentMethod(
            name="Card", code=f"card-{uuid.uuid4().hex[:8]}", type="card"
        )
        db.add_all([user, method])
        await db.flush()

        order = Order(
            order_number=f"PPC-{uuid.uuid4().hex[:12]}",
            email="pytest-pay@example.com",
            source="cashier",
            is_pos=True,
            pos_status="active",
            business_date=BUSINESS_DATE,
            branch_id=branch.id,
            status=OrderStatusEnum.CREATED,
            delivery_method=DeliveryMethodEnum.PICKUP,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
        )
        db.add(order)
        await db.commit()
        ids = (branch.id, user.id, method.id, order.id)

    yield ids

    branch_id, user_id, method_id, order_id = ids
    async with Session() as db:
        await db.execute(
            OrderPayment.__table__.delete().where(OrderPayment.order_id == order_id)
        )
        await db.execute(Order.__table__.delete().where(Order.id == order_id))
        await db.execute(
            PaymentMethod.__table__.delete().where(PaymentMethod.id == method_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_two_terminals_cannot_both_take_the_full_amount(engine, seeded):
    branch_id, user_id, method_id, order_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def pay_once() -> str:
        async with Session() as db:
            user = await db.get(User, user_id)
            order = await pos_order_service.get_order(db, order_id)
            try:
                await pos_order_service.record_payment(
                    db,
                    order=order,
                    user=user,
                    payment_method_id=method_id,
                    amount=Decimal("100.00"),
                )
                await db.commit()
                return "ok"
            except ConflictError:
                await db.rollback()
                return "rejected"

    results = sorted(await asyncio.gather(pay_once(), pay_once()))
    assert results == ["ok", "rejected"], (
        "both terminals took money into one check — the shop was overpaid"
    )

    async with Session() as db:
        rows = (
            await db.execute(
                select(
                    func.count(OrderPayment.id),
                    func.coalesce(func.sum(OrderPayment.amount), 0),
                ).where(OrderPayment.order_id == order_id)
            )
        ).one()
        count, total = rows
        assert count == 1, "exactly one payment should have landed"
        assert Decimal(str(total)) == Decimal("100.00")
