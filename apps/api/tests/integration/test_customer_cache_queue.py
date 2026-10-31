"""The customer directory's change queue (migration 317), on real Postgres.

The trigger used to `UPDATE` one singleton row, so every transaction that
wrote an order or a user held that row until it committed and every other
writer queued behind it — counter sales waited up to 92s behind a marketplace
promote. These pin the replacement: writers append and never wait on each
other, and the scheduler's pass folds the queue without losing a change from
a writer still in flight.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.customer_cache import (
    CustomerCache,
    CustomerCacheChange,
    CustomerCacheState,
)
from app.models.user import User
from app.services import customer_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
]


@pytest.fixture
async def maker():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _customer(maker) -> uuid.UUID:
    async with maker() as db:
        user = User(
            email=f"cache-{uuid.uuid4().hex[:8]}@example.com",
            display_name=f"Queue Test {uuid.uuid4().hex[:4]}",
        )
        db.add(user)
        await db.commit()
        return user.id


async def _changes(maker) -> int:
    async with maker() as db:
        return await db.scalar(select(func.count()).select_from(CustomerCacheChange))


async def test_concurrent_writers_do_not_wait_on_each_other(maker):
    first, second = await _customer(maker), await _customer(maker)
    async with maker() as a, maker() as b:
        await a.execute(
            update(User).where(User.id == first).values(display_name="Writer A")
        )
        # A has not committed. Before 317, B's trigger blocked on the state row
        # A's trigger had updated; with the queue it appends and moves on.
        await b.execute(text("SET LOCAL lock_timeout = '1s'"))
        await b.execute(
            update(User).where(User.id == second).values(display_name="Writer B")
        )
        await b.commit()
        await a.commit()


async def test_a_pass_folds_the_queue_and_rebuilds_the_directory(maker):
    user_id = await _customer(maker)
    assert await _changes(maker) > 0
    async with maker() as db:
        assert await customer_service.refresh(db, force=True) is True
        await db.commit()
    assert await _changes(maker) == 0
    async with maker() as db:
        state = await db.get(CustomerCacheState, True)
        assert state.dirty is False
        ids = (
            await db.scalars(
                select(CustomerCache.id).where(CustomerCache.name.like("Queue Test%"))
            )
        ).all()
        assert ids, "the account became a directory row"
        # Nothing changed since: a second pass is a no-op.
        assert await customer_service.refresh(db) is False
        await db.commit()
    assert user_id


async def test_a_writer_still_in_flight_dirties_the_next_pass(maker):
    user_id = await _customer(maker)
    async with maker() as db:
        await customer_service.refresh(db, force=True)
        await db.commit()

    async with maker() as writer:
        await writer.execute(
            update(User).where(User.id == user_id).values(display_name="Late")
        )
        # The pass runs while the writer is uncommitted: its change row is
        # invisible, so it must survive the fold rather than be lost.
        async with maker() as db:
            await customer_service.refresh(db, force=True)
            await db.commit()
        await writer.commit()

    assert await _changes(maker) > 0
    async with maker() as db:
        assert await customer_service.refresh(db, force=True) is True
        await db.commit()
