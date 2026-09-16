"""Two hands on one order's courier at the same instant, against a real database.

`fulfilment_reassignment._locked` takes the delivery row `FOR UPDATE` and then
holds it across up to four courier round-trips while the move cancels the old
rider and engages a new one (~tens of seconds). A plain `FOR UPDATE` would make
a second admin — or the dispatch retry sweep — *block* on a request-pool
connection for that whole stretch, waiting on somebody else's network calls, only
to find at the end that the move already changed the very thing the gates check.
So the lock is `NOWAIT` (F-COU-14): a lock that cannot be taken at once means a
move is already in progress, and the second caller is refused immediately with a
`ConflictError` rather than pinning a connection.

That is a question about lock acquisition across two connections, which a mocked
session cannot answer — it needs a real Postgres. One session holds the row lock
(standing in for the move in flight); a second calls `_locked` and must come back
refused, fast, rather than blocking until the first lets go.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models import Branch, Order, OrderDelivery
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.delivery import fulfilment_reassignment

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-reassign-concurrency"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def order(engine):
    """A confirmed delivery order with a booked Lalamove delivery row."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"prc-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        order = Order(
            order_number=f"PRC-{uuid.uuid4().hex[:12]}",
            email="pytest-reassign@example.com",
            source="online",
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
                courier_order_id=f"LM-{uuid.uuid4().hex[:10]}",
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


async def test_a_second_move_is_refused_while_the_first_holds_the_row(engine, order):
    Session = async_sessionmaker(engine, expire_on_commit=False)

    holder = Session()
    try:
        # Session A: take the row lock and keep it, as a move in flight would.
        order_a = await holder.get(Order, order)
        await fulfilment_reassignment._locked(holder, order_a)

        # Session B: the second caller. With NOWAIT it comes straight back
        # refused; `wait_for` turns a regression to a blocking `FOR UPDATE` (which
        # would hang until A commits — A never does) into a test failure instead
        # of a hang.
        async with Session() as other:
            order_b = await other.get(Order, order)
            with pytest.raises(ConflictError, match="already in progress"):
                await asyncio.wait_for(
                    fulfilment_reassignment._locked(other, order_b), timeout=5
                )
    finally:
        await holder.rollback()
        await holder.close()

    # And once the first lets go, the row is lockable again — the refusal was the
    # contention, not a stuck row.
    async with Session() as after:
        order_c = await after.get(Order, order)
        delivery = await fulfilment_reassignment._locked(after, order_c)
        assert delivery.order_id == order
        await after.rollback()


async def test_an_unlocked_read_is_never_blocked_by_a_held_lock(engine, order):
    """The reader (`lock=False`, the quote path) takes no lock and so is immune to
    a move in flight — it must return the row, not raise."""
    Session = async_sessionmaker(engine, expire_on_commit=False)

    holder = Session()
    try:
        order_a = await holder.get(Order, order)
        await fulfilment_reassignment._locked(holder, order_a)

        async with Session() as reader:
            order_b = await reader.get(Order, order)
            delivery = await asyncio.wait_for(
                fulfilment_reassignment._locked(reader, order_b, lock=False), timeout=5
            )
            assert delivery.order_id == order
            await reader.rollback()
    finally:
        await holder.rollback()
        await holder.close()
