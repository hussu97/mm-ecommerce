"""`held_session` holds ONE connection for both the lock and the sweep's work.

The whole point of `advisory_lock` is that a session-level Postgres lock belongs
to a connection, and a `Session` hands its connection back on `commit()`. `held`
solves that by pinning a SEPARATE connection for the lock — at the cost of two
scheduler connections per sweep. `held_session` folds the two into one: it yields
a Session bound to the lock's own connection. The property that has to hold, and
that only a real Postgres can prove, is that the lock stays held across the
session's `commit()` (a session-level lock outlives the transaction) while the
session's writes are still durable — and that the lock is exclusive and released.

Needs a database: set TEST_DATABASE_URL (or DATABASE_URL).
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import advisory_lock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

# A test-only key in the flat advisory namespace, distinct from every app lock.
KEY = 0x6D6D_5445_5354_0001


@pytest.fixture(autouse=True)
async def _dispose_scheduler_pool():
    """`held_session` borrows the app's module-level `scheduler_engine`, which
    otherwise keeps its pooled connections open past the test's event loop and
    trips asyncpg's teardown. Dispose it after each test so the loop closes clean."""
    yield
    from app.core.database import scheduler_engine

    await scheduler_engine.dispose()


async def test_lock_survives_the_session_commit_stays_exclusive_and_releases():
    eng = create_async_engine(DATABASE_URL)
    table = f"adv_lock_test_{uuid.uuid4().hex[:8]}"
    try:
        async with eng.begin() as c:
            await c.execute(text(f"CREATE TABLE {table} (val text)"))

        async with advisory_lock.held_session(KEY, name="holder") as db:
            assert db is not None, "first holder should get the lock and a session"

            # Write and COMMIT mid-block — the moment `held` exists to survive.
            await db.execute(text(f"INSERT INTO {table} (val) VALUES ('one')"))
            await db.commit()

            # The lock must still be ours AFTER that commit: a concurrent holder,
            # on its own connection, is turned away.
            async with advisory_lock.held_session(KEY, name="racer") as db2:
                assert db2 is None, "lock leaked after the session committed"

            # A second write on the same session, then commit again — still fine.
            await db.execute(text(f"INSERT INTO {table} (val) VALUES ('two')"))
            await db.commit()

            # The committed writes are durable — visible from a fresh connection.
            async with eng.connect() as c:
                n = await c.scalar(text(f"SELECT count(*) FROM {table}"))
                assert n == 2, "the session's committed writes were not durable"

        # Block exited → lock released → the next holder gets a live session.
        async with advisory_lock.held_session(KEY, name="next") as db3:
            assert db3 is not None, "lock was not released on block exit"
    finally:
        async with eng.begin() as c:
            await c.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await eng.dispose()


async def test_wait_true_always_yields_a_session():
    async with advisory_lock.held_session(KEY + 1, name="waiter", wait=True) as db:
        assert db is not None
        # A trivial query proves the bound session is usable.
        assert await db.scalar(text("SELECT 1")) == 1
