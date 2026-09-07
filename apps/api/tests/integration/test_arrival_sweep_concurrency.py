"""
The arrival sweep, against a real database, under the two failures F-COU-12 named.

`arrival_service.sweep` used to claim its due rows with no row lock and land the
whole batch inside one transaction the scheduler committed at the end. Two things
followed, and neither shows up against a mocked session:

  * **Double-claim.** Two schedulers on blue and green (or a bypass of the
    advisory lock) each read the same `confirmed` rows and each land them — two
    dispatches for one cake. The fix selects the rows `FOR UPDATE SKIP LOCKED`,
    so the second sweep skips what the first is holding.
  * **A batch rollback.** One order raising rolled the transaction back, undoing
    arrivals already announced to the register alongside it. The fix commits per
    order and only reports one landed once its own commit holds, so a bad order's
    blast radius is itself.

Both are questions about row locks and commit boundaries across connections,
which need a real Postgres. `asyncio.gather` over two independent sessions is the
closest a test gets to two schedulers ticking together; a widened window (a short
sleep before each landing commits) makes the unlocked version really double-claim
so the lock is what the test turns on.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.couriers import courier_service
from app.services.delivery import arrival_service
from app.services.orders import order_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-arrival-sweep"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _seed(engine, count: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """`count` confirmed online orders, all due now, sharing one branch.

    Returns the branch id and the order ids, oldest arrival first — `due()`
    orders by `arrives_at`, so seeding them a minute apart makes the sweep's
    order deterministic. Created on its own session; the sweeps open their own.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with Session() as db:
        # `branches.reference` is VARCHAR(50) and `orders.order_number` is
        # VARCHAR(30), so both take a trimmed uuid, not the full MARKER prefix.
        branch = Branch(
            name=f"{MARKER} branch", reference=f"asw-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()

        # A non-deleted branch must own exactly one default stock container at
        # commit (deferred trigger from migration 186), so give it one.
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        ids: list[uuid.UUID] = []
        for i in range(count):
            order = Order(
                order_number=f"ASW-{uuid.uuid4().hex[:12]}",
                email="pytest-arrival@example.com",
                source="online",
                branch_id=branch.id,
                status=OrderStatusEnum.CONFIRMED,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                subtotal=Decimal("100.00"),
                total=Decimal("110.00"),
                delivery_fee=Decimal("10.00"),
                # Due, and staggered so the sweep's order is deterministic.
                arrives_at=now - timedelta(minutes=count - i),
            )
            db.add(order)
            await db.flush()
            ids.append(order.id)
        await db.commit()
        return branch.id, ids


async def _teardown(engine, branch_id, order_ids) -> None:
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # Status-event rows reference the order; clear them first, then the
        # orders, then the branch's stock container and the branch.
        from app.models.inventory import Warehouse as _W
        from app.models.order_status_event import OrderStatusEvent

        await db.execute(
            OrderStatusEvent.__table__.delete().where(
                OrderStatusEvent.order_id.in_(order_ids)
            )
        )
        await db.execute(Order.__table__.delete().where(Order.id.in_(order_ids)))
        await db.execute(_W.__table__.delete().where(_W.branch_id == branch_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_two_concurrent_sweeps_land_each_order_once(engine, monkeypatch):
    """Two sweeps racing over the same due rows land each order exactly once."""
    branch_id, order_ids = await _seed(engine, count=2)
    try:
        # Only the claim is under test: the register publish and the courier
        # booking are stubbed, the booking with a sleep that widens the window so
        # that without `SKIP LOCKED` the second sweep really would re-claim the
        # rows the first is still holding.
        async def noop_publish(db, order):
            return None

        async def slow_dispatch(db, order, **kwargs):
            await asyncio.sleep(0.2)
            return None

        monkeypatch.setattr(order_service, "publish_to_register", noop_publish)
        monkeypatch.setattr(courier_service, "dispatch", slow_dispatch)

        Session = async_sessionmaker(engine, expire_on_commit=False)

        async def sweep_once():
            async with Session() as db:
                return await arrival_service.sweep(db)

        first, second = await asyncio.gather(sweep_once(), sweep_once())

        landed = first + second
        # Each due order landed once and only once, across both sweeps.
        assert len(landed) == len(set(landed)) == len(order_ids)

        # And every order actually reached the register in the database.
        async with Session() as db:
            rows = (
                (await db.execute(select(Order.status).where(Order.id.in_(order_ids))))
                .scalars()
                .all()
            )
        assert all(s == OrderStatusEnum.ARRIVED_AT_POS for s in rows)
    finally:
        await _teardown(engine, branch_id, order_ids)


async def test_one_bad_order_does_not_roll_back_the_others(engine, monkeypatch):
    """A single order raising during landing leaves the rest committed."""
    branch_id, order_ids = await _seed(engine, count=3)
    good_first, bad, good_last = order_ids
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)

        # The middle order blows up while landing; the two either side must still
        # be committed as arrived. A batch-wide transaction would roll the first
        # one back when the middle raises — the F-COU-12 failure.
        async def flaky_publish(db, order):
            if order.id == bad:
                raise RuntimeError("register unreachable for this one")
            return None

        async def noop_dispatch(db, order, **kwargs):
            return None

        monkeypatch.setattr(order_service, "publish_to_register", flaky_publish)
        monkeypatch.setattr(courier_service, "dispatch", noop_dispatch)

        async with Session() as db:
            landed = await arrival_service.sweep(db)

        # Read the committed truth on a fresh session.
        async with Session() as db:
            statuses = dict(
                (
                    await db.execute(
                        select(Order.id, Order.status).where(Order.id.in_(order_ids))
                    )
                ).all()
            )

        assert statuses[good_first] == OrderStatusEnum.ARRIVED_AT_POS
        assert statuses[good_last] == OrderStatusEnum.ARRIVED_AT_POS
        # The bad order's own work rolled back; it stays confirmed for the next
        # tick and is not reported landed.
        assert statuses[bad] == OrderStatusEnum.CONFIRMED
        bad_order = await _order_number(engine, bad)
        assert bad_order not in landed
    finally:
        await _teardown(engine, branch_id, order_ids)


async def _order_number(engine, order_id) -> str:
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        return (
            await db.execute(select(Order.order_number).where(Order.id == order_id))
        ).scalar_one()
