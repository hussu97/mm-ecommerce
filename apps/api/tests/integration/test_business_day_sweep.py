"""
A trading day that rolled over before it was closed must still get closed.

F-POS-26: `close_current` only ever looked at the branch's *current* business
date, so a day nobody closed before the cut-off passed became unreachable and
stayed `open` forever. The fix gives `close_current` an optional `business_date`
and adds `sweep_stale_business_days`, which closes those stranded days — unless a
till is still open on one, which is left for the ordinary close. Against a real
Postgres because it is about which BranchBusinessDay rows the sweep selects.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch
from app.models.branch import BranchBusinessDay
from app.models.inventory import Warehouse
from app.models.till import Till
from app.models.user import User
from app.services.pos import business_day_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-bday-sweep"
# now = UTC noon → 16:00 Dubai → minus the 04:00 cut-off → business date 08-21.
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
CURRENT = "2026-08-21"
STRANDED = "2026-08-20"
STRANDED_WITH_TILL = "2026-08-19"


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
        user = User(email=f"sweep-{uuid.uuid4().hex[:8]}@example.com")
        db.add(user)
        await db.flush()

        opened = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
        for d in (STRANDED_WITH_TILL, STRANDED, CURRENT):
            db.add(
                BranchBusinessDay(
                    branch_id=branch.id, business_date=d, opened_at=opened
                )
            )
        # An open till only on the 19th — that stranded day must be left alone.
        db.add(
            Till(
                branch_id=branch.id,
                user_id=user.id,
                business_date=STRANDED_WITH_TILL,
                status="open",
                opened_at=opened,
            )
        )
        await db.commit()
        ids = (branch.id, user.id)

    yield ids

    async with Session() as db:
        await db.execute(Till.__table__.delete().where(Till.branch_id == ids[0]))
        await db.execute(
            BranchBusinessDay.__table__.delete().where(
                BranchBusinessDay.branch_id == ids[0]
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids[0])
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == ids[0]))
        await db.execute(User.__table__.delete().where(User.id == ids[1]))
        await db.commit()


async def _day(db, branch_id, date):
    from sqlalchemy import select

    return (
        await db.execute(
            select(BranchBusinessDay).where(
                BranchBusinessDay.branch_id == branch_id,
                BranchBusinessDay.business_date == date,
            )
        )
    ).scalar_one()


async def test_sweep_closes_stranded_days_but_not_the_current_or_till_held_one(
    seeded, engine
):
    branch_id, _ = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        closed = await business_day_service.sweep_stale_business_days(db, now=NOW)
        await db.commit()

        stranded = await _day(db, branch_id, STRANDED)
        current = await _day(db, branch_id, CURRENT)
        with_till = await _day(db, branch_id, STRANDED_WITH_TILL)

    # The rolled-over day with no open till is closed.
    assert stranded.closed_at is not None
    assert stranded.closed_by_id is None  # a system close, no cashier's name
    assert f"{branch_id}:{STRANDED}" in closed

    # The current day is still open — its trading is not over.
    assert current.closed_at is None
    # The stranded day that still holds an open till is left for the ordinary close.
    assert with_till.closed_at is None
    assert f"{branch_id}:{STRANDED_WITH_TILL}" not in closed


async def test_close_current_can_close_a_named_past_day(seeded, engine):
    """The optional `business_date` param closes a specific earlier day directly —
    the reach `close_current` lacked before."""
    branch_id, _ = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = await db.get(Branch, branch_id)
        day = await business_day_service.close_current(
            db, branch, business_date=STRANDED
        )
        await db.commit()
        assert day.business_date == STRANDED
        assert day.closed_at is not None
