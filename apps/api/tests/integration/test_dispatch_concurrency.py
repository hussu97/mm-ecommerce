"""
Two dispatches of one order, at the same instant, against a real database.

`POST /orders/{n}/delivery/dispatch` took no row lock (F-COU-3), so two admins —
or an admin and the retry sweep — could each read an unbooked delivery row and
each book a courier, putting two drivers on one cake. The fix reads the row
`FOR UPDATE` in `courier_service.dispatch` and re-checks `courier_order_id` under
the lock, so the second caller waits, sees the first one's booking, and turns
back.

That is a question about row locks holding across two connections, which a
mocked session cannot answer — it needs a real Postgres. `asyncio.gather` over
two independent sessions is the closest a test gets to two admins pressing the
button together; a spy standing in for the network booking counts how many
couriers were actually engaged, and widens the window with a short sleep so that
without the lock both callers really would book.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order, OrderDelivery
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.couriers import courier_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-dispatch-concurrency"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def order(engine):
    """A confirmed delivery order in a Lalamove zone, with an unbooked row.

    Created and cleaned up on its own session; the test itself opens two more.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # `branches.reference` is VARCHAR(50); MARKER (27) + a full uuid (36)
        # overruns it, so trim the suffix like `order_number` does below.
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()

        order = Order(
            order_number=f"{MARKER}-{uuid.uuid4().hex[:12]}",
            branch_id=branch.id,
            status=OrderStatusEnum.CONFIRMED,
            delivery_method=DeliveryMethodEnum.DELIVERY,
            subtotal=Decimal("100.00"),
            total=Decimal("110.00"),
            delivery_fee=Decimal("10.00"),
            shipping_address_snapshot={
                "latitude": 25.20,
                "longitude": 55.27,
                "phone": "+971501234567",
                "city": "Dubai",
            },
        )
        db.add(order)
        await db.flush()

        db.add(
            OrderDelivery(
                order_id=order.id,
                provider="lalamove",
                zone_name="Dubai Marina",
                fee_charged=Decimal("10.00"),
            )
        )
        await db.commit()
        order_id, branch_id = order.id, branch.id

    yield order_id

    async with Session() as db:
        await db.execute(
            OrderDelivery.__table__.delete().where(OrderDelivery.order_id == order_id)
        )
        await db.execute(Order.__table__.delete().where(Order.id == order_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_two_concurrent_dispatches_book_one_courier(engine, order, monkeypatch):
    bookings: list[str] = []

    async def spy_dispatch_order(db, order):
        delivery = await courier_service.lalamove_service.get_delivery(db, order.id)
        # Widen the window: without the FOR UPDATE lock the second caller would
        # read the same unbooked row here and book a second courier.
        await asyncio.sleep(0.2)
        bookings.append("lalamove")
        delivery.courier_order_id = f"LM-{uuid.uuid4().hex[:10]}"
        delivery.last_error = None
        # The real dispatchers commit the booking themselves — a rider has been
        # engaged outside our transaction — which is what releases the row lock.
        await db.commit()
        return delivery

    monkeypatch.setattr(
        courier_service.lalamove_service, "dispatch_order", spy_dispatch_order
    )

    # A booking stamps the order packed, which re-enters the whole lifecycle; that
    # chain is exercised by the unit test. Here only the lock is under test, so
    # the stamp is a no-op.
    from app.services.orders import order_service

    async def noop_stamp_packed(db, order, *, note):
        return False

    monkeypatch.setattr(order_service, "stamp_packed", noop_stamp_packed)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def dispatch_once():
        async with Session() as db:
            order_row = await db.get(Order, order)
            await courier_service.dispatch(db, order_row)
            await db.commit()

    await asyncio.gather(dispatch_once(), dispatch_once())

    assert bookings == ["lalamove"]

    async with Session() as db:
        delivery = (
            await db.execute(
                select(OrderDelivery).where(OrderDelivery.order_id == order)
            )
        ).scalar_one()
        assert delivery.courier_order_id is not None
