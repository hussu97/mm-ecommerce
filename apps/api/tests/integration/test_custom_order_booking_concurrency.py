"""
Two bookings of one date's last slot, at the same instant, against a real DB.

Custom-order capacity was count-then-insert with no lock: two requests for the
last slot on a day each read "one free" before either had written, and the date
was promised twice. A date has no single row to lock — capacity is a count — so
the guard serialises on the *date*, taking a transaction-scoped advisory lock
(`advisory_lock.held_for_request`) around the check and the insert. The second
caller waits for the first to commit, counts the now-visible booking, and is
refused.

That is a question about a lock holding across two connections, which a mocked
session cannot answer — it needs a real Postgres. `asyncio.gather` over two
independent sessions is the closest a test gets to two bookings racing. SKIPs
unless `TEST_DATABASE_URL` is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import ConflictError
from app.models import BusinessSettings, CustomOrder
from app.services import custom_order_service as svc

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = f"pytest-cocc-{uuid.uuid4().hex[:8]}-"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def one_per_day(engine):
    """Capacity of exactly one, stated rather than assumed."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        settings = (
            (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
        )
        if settings is None:
            settings = BusinessSettings()
            db.add(settings)
        settings.custom_orders_per_day = 1
        settings.custom_order_lead_days = 3
        await db.commit()

    yield

    async with Session() as db:
        await db.execute(
            delete(CustomOrder).where(CustomOrder.customer_name.like(f"{MARKER}%"))
        )
        await db.commit()


async def test_two_bookings_of_the_last_slot_seat_one(engine, one_per_day):
    day = date.today() + timedelta(days=120)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def book_once(who: str):
        async with Session() as db:
            order = await svc.book(
                db,
                due_date=day,
                customer_name=f"{MARKER}{who}",
                description="Two-tier pistachio with gold leaf",
                source="instagram",
            )
            # Widen the window: without the lock the second caller would already
            # have passed its capacity check here and would also seat a booking.
            await asyncio.sleep(0.2)
            await db.commit()
            return order.id

    results = await asyncio.gather(
        book_once("aisha"), book_once("omar"), return_exceptions=True
    )

    seated = [r for r in results if isinstance(r, uuid.UUID)]
    refused = [r for r in results if isinstance(r, ConflictError)]

    assert len(seated) == 1, f"exactly one booking may take the slot: {results}"
    assert len(refused) == 1, f"the loser is refused, not crashed: {results}"
    assert "fully booked" in str(refused[0])

    async with Session() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(CustomOrder)
                .where(CustomOrder.due_date == day)
            )
        ).scalar()
        assert count == 1
