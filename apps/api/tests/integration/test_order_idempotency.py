"""
`POST /orders` is idempotent on `client_request_id`, against a real DB (F-WEB-5).

A create that times out leaves the browser unable to tell a lost request from a
lost response, so it replays the same `client_request_id`. Without this, the
replay became a second order — the double-order-on-mobile bug. Three guarantees,
all of which need a real Postgres (a mocked session cannot hold a unique index
across two connections):

  * a replay of a create that already succeeded returns that same order and
    writes no second one — `create_order` short-circuits on the key before it
    even looks at the cart, which a successful first create has cleared;
  * the partial unique index makes a second row with the same non-null key a
    caught conflict, while any number of NULL keys (every counter and aggregator
    order) coexist;
  * two truly concurrent creates of one attempt settle to exactly one order.

SKIPs unless `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.order import OrderCreate
from app.services.orders import order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

TAG = uuid.uuid4().hex[:10]
EMAIL = f"idem-{TAG}@example.com"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(branch_id, *, client_request_id=None, order_number=None) -> Order:
    return Order(
        order_number=order_number or f"IDEM-{uuid.uuid4().hex[:12]}",
        client_request_id=client_request_id,
        email=EMAIL,
        source="online",
        branch_id=branch_id,
        status=OrderStatusEnum.CREATED,
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal("10.00"),
        total=Decimal("10.00"),
        delivery_fee=Decimal("0.00"),
    )


@pytest.fixture
async def branch_id(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(name=f"idem-{TAG}", reference=f"idem-{TAG}")
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
        await db.commit()
        bid = branch.id

    yield bid

    async with Session() as db:
        await db.execute(delete(Order).where(Order.email == EMAIL))
        await db.execute(delete(Branch).where(Branch.id == bid))
        await db.commit()


async def test_a_replayed_client_request_id_returns_the_same_order(engine, branch_id):
    """The heart of it: a second create with the same key returns the first
    order and does not write a duplicate — proven without ever seeding a cart,
    because the idempotency check runs before the cart is even located."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    cid = uuid.uuid4()

    async with Session() as db:
        first = _order(branch_id, client_request_id=cid)
        db.add(first)
        await db.commit()
        first_number = first.order_number

    async with Session() as db:
        data = OrderCreate(
            email=EMAIL,
            delivery_method=DeliveryMethodEnum.PICKUP,
            payment_method="cod",
            client_request_id=cid,
            # A pickup order now carries the collecting customer's contact.
            pickup_contact={
                "first_name": "Idem",
                "last_name": "Customer",
                "phone": "+971501234567",
            },
        )
        response = await order_service.create_order(db, data, user_id=None)
        # The order the first create wrote, handed straight back.
        assert response.order_number == first_number

    async with Session() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.client_request_id == cid)
            )
        ).scalar()
        assert count == 1, "the replay must not write a second order"


async def test_the_index_forbids_a_duplicate_key_but_allows_many_nulls(
    engine, branch_id
):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    cid = uuid.uuid4()

    async with Session() as db:
        db.add(_order(branch_id, client_request_id=cid))
        await db.commit()

    # A second row with the same key is refused by the partial unique index.
    async with Session() as db:
        db.add(_order(branch_id, client_request_id=cid))
        with pytest.raises(IntegrityError):
            await db.commit()

    # But NULL keys — every counter and aggregator order — coexist freely.
    async with Session() as db:
        db.add(_order(branch_id, client_request_id=None))
        db.add(_order(branch_id, client_request_id=None))
        await db.commit()  # no raise


async def test_two_concurrent_creates_of_one_attempt_settle_to_one_order(
    engine, branch_id
):
    """The race the check-then-insert cannot cover on its own: two inserts of the
    same key at the same instant. The index lets exactly one through; the other
    raises, which is the signal `create_order` turns into an adopt."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    cid = uuid.uuid4()

    async def insert_once() -> str:
        async with Session() as db:
            db.add(_order(branch_id, client_request_id=cid))
            # Widen the window so both are past the (absent) check and racing at
            # the write, the way two taps a few milliseconds apart would.
            await asyncio.sleep(0.2)
            await db.commit()
            return "written"

    results = await asyncio.gather(insert_once(), insert_once(), return_exceptions=True)
    written = [r for r in results if r == "written"]
    conflicts = [r for r in results if isinstance(r, IntegrityError)]
    assert len(written) == 1, f"exactly one insert may win: {results}"
    assert len(conflicts) == 1, f"the loser conflicts, not crashes: {results}"

    async with Session() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.client_request_id == cid)
            )
        ).scalar()
        assert count == 1
