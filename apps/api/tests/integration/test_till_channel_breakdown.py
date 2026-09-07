"""
Two tills open at one branch must not each report the whole branch's revenue.

F-POS-22: `_channel_breakdown` scoped by branch + open window, so when two tills
were open together each reported ALL of the branch's takings and a close-out
double-counted the day. The fix attributes a counter sale to the till that rang
it up (`till_id`) and an un-tilled online/marketplace order to the single till
that was open when it arrived (`_covering_till`). Verified against a real
Postgres because the LATERAL and the per-till scoping only exist in the SQL.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.device import Device
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.till import Till
from app.models.user import User
from app.services.pos import till_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-till-breakdown"
BDATE = "2026-09-05"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch",
            reference=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            business_day_start="04:00",
            is_active=True,
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        u_a = User(email=f"a-{uuid.uuid4().hex[:8]}@example.com")
        u_b = User(email=f"b-{uuid.uuid4().hex[:8]}@example.com")
        dev_a = Device(
            name="Reg A",
            reference=f"A-{uuid.uuid4().hex[:8]}",
            type="register",
            branch_id=branch.id,
        )
        dev_b = Device(
            name="Reg B",
            reference=f"B-{uuid.uuid4().hex[:8]}",
            type="register",
            branch_id=branch.id,
        )
        db.add_all([u_a, u_b, dev_a, dev_b])
        await db.flush()

        # A opened first, B a minute later — so B is the covering till for the
        # un-tilled website order (the most recently opened covering till wins).
        t0 = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
        till_a = Till(
            branch_id=branch.id,
            user_id=u_a.id,
            device_id=dev_a.id,
            business_date=BDATE,
            status="open",
            opened_at=t0,
        )
        till_b = Till(
            branch_id=branch.id,
            user_id=u_b.id,
            device_id=dev_b.id,
            business_date=BDATE,
            status="open",
            opened_at=t0.replace(minute=1),
        )
        db.add_all([till_a, till_b])
        await db.flush()

        when = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

        def counter(till_id, total):
            return Order(
                order_number=f"TC-{uuid.uuid4().hex[:14]}",
                email="pytest-till@example.com",
                source="cashier",
                branch_id=branch.id,
                till_id=till_id,
                is_pos=True,
                business_date=BDATE,
                status=OrderStatusEnum.DELIVERED.value,
                pos_status="closed",
                closed_at=when,
                delivery_method=DeliveryMethodEnum.PICKUP,
                subtotal=Decimal(str(total)),
                total=Decimal(str(total)),
            )

        website = Order(
            order_number=f"TW-{uuid.uuid4().hex[:14]}",
            email="pytest-web@example.com",
            source="online",
            branch_id=branch.id,
            till_id=None,
            is_pos=True,
            business_date=BDATE,
            status=OrderStatusEnum.DELIVERED.value,
            delivery_method=DeliveryMethodEnum.DELIVERY,
            subtotal=Decimal("50.00"),
            total=Decimal("50.00"),
        )
        website.created_at = when
        db.add_all(
            [counter(till_a.id, "100.00"), counter(till_b.id, "200.00"), website]
        )
        await db.commit()
        ids = (branch.id, till_a.id, till_b.id)

    yield ids

    async with Session() as db:
        await db.execute(Order.__table__.delete().where(Order.branch_id == ids[0]))
        await db.execute(Till.__table__.delete().where(Till.branch_id == ids[0]))
        await db.execute(Device.__table__.delete().where(Device.branch_id == ids[0]))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids[0])
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == ids[0]))
        await db.commit()


async def test_two_open_tills_split_the_branch_instead_of_double_counting(
    seeded, engine
):
    branch_id, till_a_id, till_b_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        till_a = await db.get(Till, till_a_id)
        till_b = await db.get(Till, till_b_id)
        a = await till_service._channel_breakdown(db, till_a)
        b = await till_service._channel_breakdown(db, till_b)

    # Till A: only its own counter sale (100). Not B's, not the website order.
    assert a["total_revenue"] == Decimal("100.00")
    # Till B: its own counter sale (200) plus the un-tilled website order (50) it
    # was covering.
    assert b["total_revenue"] == Decimal("250.00")

    # Neither till claims the whole branch, and the two sum to the true total once.
    assert a["total_revenue"] + b["total_revenue"] == Decimal("350.00")

    # The website revenue is a branch-attributed row on B, not a till-own one.
    web = next(c for c in b["channels"] if c["key"] == "online")
    assert web["attribution"] == "branch"
    counter_row = next(c for c in b["channels"] if c["key"] == "cashier")
    assert counter_row["attribution"] == "till"
