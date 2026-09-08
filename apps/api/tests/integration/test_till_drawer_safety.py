"""Cash always reaches a drawer, and a close serialises with a payment.

F-POS-15 — a cash tender with no `till` used to record `OrderPayment.till_id`
NULL and skip the drawer ledger entirely, so the till never saw the money and
could not reconcile (prod had 6 such rows). Cash now resolves the cashier's own
open drawer, and is refused when there is none.

F-POS-23 — `close_till` snapshotted `estimated_cash` from the ledger and flipped
the status with no row lock, so a payment's `add_drawer_operation` committing in
the gap left the shift's variance computed against a stale total. Both now take
`SELECT … FOR UPDATE` on the till row and serialise.

Row locks across connections need a real Postgres; two sessions under
`asyncio.gather` stand in for two terminals.
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
from app.core.money import money
from app.models import Branch, Till, User
from app.models.base import utcnow
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.payment_method import PaymentMethod
from app.models.pos_order import OrderPayment
from app.models.till import DrawerOperation, DrawerOperationTypeEnum, TillStatusEnum
from app.services.pos import pos_order_service, till_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-till-drawer"
BUSINESS_DATE = "2026-09-08"
OPENING = Decimal("200.00")
_SALES = DrawerOperationTypeEnum.SALES.value


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    """A cashier with an open drawer, a cash method, an active counter check, and
    a second cashier who has NO drawer open."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        cashier = User(email=f"cash-{uuid.uuid4().hex[:10]}@example.com", is_staff=True)
        stranger = User(email=f"str-{uuid.uuid4().hex[:10]}@example.com", is_staff=True)
        method = PaymentMethod(
            name="Cash", code=f"cash-{uuid.uuid4().hex[:8]}", type="cash"
        )
        db.add_all([cashier, stranger, method])
        await db.flush()

        till = Till(
            branch_id=branch.id,
            user_id=cashier.id,
            business_date=BUSINESS_DATE,
            status=TillStatusEnum.OPEN.value,
            opening_amount=OPENING,
            estimated_cash=OPENING,
            variance=Decimal("0.00"),
            opened_at=utcnow(),
        )
        db.add(till)

        order = Order(
            order_number=f"TDS-{uuid.uuid4().hex[:12]}",
            email="pytest-till@example.com",
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
        ids = dict(
            branch_id=branch.id,
            cashier_id=cashier.id,
            stranger_id=stranger.id,
            method_id=method.id,
            till_id=till.id,
            order_id=order.id,
        )

    yield ids

    async with Session() as db:
        await db.execute(
            DrawerOperation.__table__.delete().where(
                DrawerOperation.till_id == ids["till_id"]
            )
        )
        await db.execute(
            OrderPayment.__table__.delete().where(
                OrderPayment.order_id == ids["order_id"]
            )
        )
        await db.execute(Order.__table__.delete().where(Order.id == ids["order_id"]))
        await db.execute(Till.__table__.delete().where(Till.id == ids["till_id"]))
        await db.execute(
            PaymentMethod.__table__.delete().where(PaymentMethod.id == ids["method_id"])
        )
        await db.execute(
            User.__table__.delete().where(
                User.id.in_([ids["cashier_id"], ids["stranger_id"]])
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids["branch_id"])
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == ids["branch_id"]))
        await db.commit()


async def test_cash_with_no_till_lands_in_the_cashiers_open_drawer(engine, seeded):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        cashier = await db.get(User, seeded["cashier_id"])
        order = await pos_order_service.get_order(db, seeded["order_id"])
        # No `till` handed in — the terminal omitted it.
        payment = await pos_order_service.record_payment(
            db,
            order=order,
            user=cashier,
            payment_method_id=seeded["method_id"],
            amount=Decimal("100.00"),
        )
        await db.commit()
        payment_till = payment.till_id

    assert payment_till == seeded["till_id"], (
        "the cash payment was not stamped with the cashier's open drawer"
    )
    async with Session() as db:
        drawer_total = (
            await db.execute(
                select(func.coalesce(func.sum(DrawerOperation.amount), 0)).where(
                    DrawerOperation.till_id == seeded["till_id"],
                    DrawerOperation.type == _SALES,
                )
            )
        ).scalar()
        assert money(drawer_total) == Decimal("100.00"), (
            "the cash never reached the drawer ledger"
        )


async def test_cash_is_refused_when_the_cashier_has_no_open_drawer(engine, seeded):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        stranger = await db.get(User, seeded["stranger_id"])  # no open till
        order = await pos_order_service.get_order(db, seeded["order_id"])
        with pytest.raises(ConflictError):
            await pos_order_service.record_payment(
                db,
                order=order,
                user=stranger,
                payment_method_id=seeded["method_id"],
                amount=Decimal("100.00"),
            )
        await db.rollback()


async def test_closing_a_till_serialises_with_a_drawer_write(engine, seeded):
    """A close and a drawer write race. Whatever the order, a closed till's
    `estimated_cash` must equal opening + every committed sales op — never a
    snapshot that missed a payment committed in the gap."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    till_id = seeded["till_id"]

    async def close() -> str:
        async with Session() as db:
            user = await db.get(User, seeded["cashier_id"])
            till = await db.get(Till, till_id)
            try:
                await till_service.close_till(
                    db, till=till, closed_by=user, closing_amount=Decimal("250.00")
                )
                await db.commit()
                return "closed"
            except ConflictError:
                await db.rollback()
                return "close-conflict"

    async def drawer() -> str:
        async with Session() as db:
            user = await db.get(User, seeded["cashier_id"])
            till = await db.get(Till, till_id)
            try:
                await till_service.add_drawer_operation(
                    db,
                    till=till,
                    user=user,
                    op_type=_SALES,
                    amount=Decimal("50.00"),
                )
                await db.commit()
                return "recorded"
            except ConflictError:
                await db.rollback()
                return "drawer-conflict"

    await asyncio.gather(close(), drawer())

    async with Session() as db:
        till = await db.get(Till, till_id)
        committed_sales = (
            await db.execute(
                select(func.coalesce(func.sum(DrawerOperation.amount), 0)).where(
                    DrawerOperation.till_id == till_id,
                    DrawerOperation.type == _SALES,
                )
            )
        ).scalar()
        if till.status == TillStatusEnum.CLOSED.value:
            assert money(till.estimated_cash) == money(
                OPENING + money(committed_sales)
            ), (
                "the close snapshotted a drawer total that a committed payment "
                "had already changed — the lock did not serialise them"
            )
