"""One sequence number per order's kitchen tickets, against a real Postgres (F-POS-31).

`send_to_kitchen` numbers each new ticket after the highest the order already
has. Two fires racing used to both read the same mark and write the same number,
leaving two "#2" tickets the KDS could not tell apart. The fix is a
`(order_id, sequence)` unique constraint (migration 219) the service retries
against inside a savepoint. These check the two halves a mocked session cannot:

- the numbers the service actually assigns are sequential and gap-free across
  repeated fires of the same check;
- the constraint itself rejects a duplicate `(order_id, sequence)` at the DB, so
  the race it guards against is a caught error rather than silent corruption.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import Order, OrderItem
from app.models.pos_order import (
    KitchenTicket,
    KitchenTicketStatusEnum,
    OrderSourceEnum,
    PosOrderStatusEnum,
)
from app.services.pos import pos_order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-kds-seq"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _item(name: str) -> OrderItem:
    return OrderItem(
        product_name=name,
        product_sku=f"{MARKER}-{uuid.uuid4().hex[:8]}",
        quantity=1,
        base_price=Decimal("10"),
        unit_price=Decimal("10"),
        total_price=Decimal("10"),
    )


@pytest.fixture
async def order_id(engine):
    """A single open counter check with one unsent line, on its own branch."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        # An active branch must own exactly one default stock container (trigger).
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))
        order = Order(
            order_number=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            email=f"{MARKER}@example.com",
            delivery_method="pickup",
            subtotal=Decimal("10"),
            total=Decimal("10"),
            branch_id=branch.id,
            source=OrderSourceEnum.CASHIER.value,
            pos_status=PosOrderStatusEnum.ACTIVE.value,
        )
        order.items.append(_item("Brownie"))
        db.add(order)
        await db.commit()
        oid, bid = order.id, branch.id

    yield oid

    async with Session() as db:
        await db.execute(
            KitchenTicket.__table__.delete().where(KitchenTicket.order_id == oid)
        )
        await db.execute(OrderItem.__table__.delete().where(OrderItem.order_id == oid))
        await db.execute(Order.__table__.delete().where(Order.id == oid))
        await db.execute(Warehouse.__table__.delete().where(Warehouse.branch_id == bid))
        await db.execute(Branch.__table__.delete().where(Branch.id == bid))
        await db.commit()


async def _load(db, order_id):
    from sqlalchemy.orm import selectinload

    return (
        await db.execute(
            select(Order).where(Order.id == order_id).options(selectinload(Order.items))
        )
    ).scalar_one()


async def test_repeated_fires_number_the_tickets_sequentially(engine, order_id):
    """A check fired, added to, and fired again gets tickets #1 then #2 — the
    second read of the high-water mark sees the first ticket and follows it."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        order = await _load(db, order_id)
        first = await pos_order_service.send_to_kitchen(db, order=order)
        await db.commit()
        assert [t.sequence for t in first] == [1]

    async with Session() as db:
        order = await _load(db, order_id)
        order.items.append(_item("Cheesecake"))
        await db.flush()
        second = await pos_order_service.send_to_kitchen(db, order=order)
        await db.commit()
        assert [t.sequence for t in second] == [2]

    async with Session() as db:
        rows = (
            (
                await db.execute(
                    select(KitchenTicket.sequence)
                    .where(KitchenTicket.order_id == order_id)
                    .order_by(KitchenTicket.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert rows == [1, 2]


async def test_the_constraint_rejects_a_duplicate_sequence(engine, order_id):
    """The durable guarantee behind the retry: the DB itself refuses a second
    ticket at a sequence the order already uses."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        order = await _load(db, order_id)
        await pos_order_service.send_to_kitchen(db, order=order)
        await db.commit()

    async with Session() as db:
        db.add(
            KitchenTicket(
                order_id=order_id,
                branch_id=(await _load(db, order_id)).branch_id,
                sequence=1,
                status=KitchenTicketStatusEnum.NEW.value,
                sent_at=utcnow(),
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
