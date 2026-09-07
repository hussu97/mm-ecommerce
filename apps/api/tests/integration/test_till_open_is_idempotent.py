"""
Opening a till is idempotent under a race, against a real database (F-POS-5).

`till_service.open_till` is read-then-insert: it checks for an existing open till,
then inserts. Two terminals (or one retried request) could each read "no open
till" and both insert, splitting a cashier's takings across two reconciliations.
Migration 202 adds a partial unique index (one `open` till per `user_id`); the
service catches the `IntegrityError` the loser now gets and returns the winner.

That is a question about a unique index holding across two connections, which a
mocked session cannot answer. Two sessions under `asyncio.gather` are the closest
a test gets to two terminals opening at once; `get_or_open` is slowed so both
callers clear the pre-check before either inserts, the window the index guards.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, User
from app.models.base import utcnow
from app.models.branch import BranchBusinessDay
from app.models.inventory import Warehouse
from app.models.till import Till
from app.services.pos import business_day_service, till_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-till-idempotent"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def branch_and_user(engine):
    """A branch, a staff user, and a pre-opened business day.

    The business day is created up front so the racing opens both find it —
    leaving the till insert as the only contended write.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        user = User(email=f"till-{uuid.uuid4().hex[:10]}@example.com", is_staff=True)
        db.add(user)
        await db.flush()

        business_date = await business_day_service.current_business_date(db, branch)
        db.add(
            BranchBusinessDay(
                branch_id=branch.id,
                business_date=business_date,
                opened_at=utcnow(),
                opened_by_id=user.id,
            )
        )
        await db.commit()
        branch_id, user_id = branch.id, user.id

    yield branch_id, user_id

    async with Session() as db:
        await db.execute(Till.__table__.delete().where(Till.user_id == user_id))
        await db.execute(
            BranchBusinessDay.__table__.delete().where(
                BranchBusinessDay.branch_id == branch_id
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_two_concurrent_opens_make_one_till(engine, branch_and_user, monkeypatch):
    branch_id, user_id = branch_and_user

    # Widen the window: both callers must clear the "no open till" pre-check
    # before either inserts, so the partial unique index is what decides the race.
    original = business_day_service.get_or_open

    async def slow_get_or_open(db, branch, *, opened_by=None):
        await asyncio.sleep(0.2)
        return await original(db, branch, opened_by=opened_by)

    monkeypatch.setattr(business_day_service, "get_or_open", slow_get_or_open)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def open_once() -> uuid.UUID:
        async with Session() as db:
            user = await db.get(User, user_id)
            branch = await db.get(Branch, branch_id)
            till = await till_service.open_till(
                db,
                user=user,
                branch=branch,
                device_id=None,
                opening_amount=Decimal("100.00"),
            )
            till_id = till.id
            await db.commit()
            return till_id

    first, second = await asyncio.gather(open_once(), open_once())

    # Both callers got a till, and it is the SAME till — the loser was handed the
    # winner's row, not an error.
    assert first == second

    async with Session() as db:
        count = (
            await db.execute(
                select(func.count(Till.id)).where(
                    Till.user_id == user_id, Till.status == "open"
                )
            )
        ).scalar_one()
        assert count == 1, "a second open till slipped past the unique index"
