"""
A named lock only one worker holds at a time, pinned to its own connection.

Postgres advisory locks are **session-scoped**: the lock belongs to the
connection that took it, and only that connection can give it back. That is the
whole difficulty, because nothing else in this app owns a connection — work runs
through `AsyncSessionFactory`, and a `Session` hands its connection back to the
pool on every `commit()`. So a sweep written the obvious way

    async with AsyncSessionFactory() as session:
        await session.scalar(text("SELECT pg_try_advisory_lock(:key)"), ...)
        ...work, which commits...
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), ...)

takes the lock on whichever connection the pool happened to give it, commits —
returning that connection to the pool — and then runs the unlock on whatever
connection it is handed next. When the pool is quiet that is the same one and
the unlock works. When the pool is busy it is a different one, the unlock
returns false, and the lock stays on the original connection until it is
recycled an hour later.

Nothing raises. `pg_try_advisory_lock` is a *try* lock, so every later sweep
takes its `False` as "another worker is on it" and returns quietly. The loop
keeps ticking and does nothing at all, which is how production spent an
afternoon with a dispatcher that logged `Batch dispatcher started` and then
never sent a run. See `batch_scheduler`.

Holding the lock on a connection of its own is the fix, and it has to be an
`AsyncConnection` rather than a `Session`: a connection checked out with
`engine.connect()` stays checked out for the life of the block whether or not it
commits, so the lock and the unlock are guaranteed to be the same connection.

The transaction is committed the moment the lock is taken, deliberately. The
work inside can take as long as a courier's API does, and leaving this
connection `idle in transaction` for all of it would pin the database's xmin
horizon behind a connection that is doing nothing.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.core.database import engine

logger = logging.getLogger(__name__)

__all__ = ["held"]

_ACQUIRE = text("SELECT pg_try_advisory_lock(:key)")
_RELEASE = text("SELECT pg_advisory_unlock(:key)")


@asynccontextmanager
async def held(key: int, *, name: str) -> AsyncIterator[bool]:
    """
    Yield whether this worker got the lock, and release it on the way out.

    `False` is an ordinary outcome, not a failure: it means another worker is
    already inside the sweep. Callers do nothing and wait for the next tick.
    """
    async with engine.connect() as conn:
        got = bool(await conn.scalar(_ACQUIRE, {"key": key}))
        # Ends the transaction without giving the connection back — which is the
        # difference between `Connection` and `Session`, and the reason this
        # module exists.
        await conn.commit()

        if not got:
            yield False
            return

        try:
            yield True
        finally:
            released = bool(await conn.scalar(_RELEASE, {"key": key}))
            await conn.commit()
            if not released:  # pragma: no cover — the bug this module prevents
                logger.error(
                    "Advisory lock %s (%s) was not held by the connection "
                    "releasing it; the sweep may be wedged until this "
                    "connection is recycled",
                    key,
                    name,
                )
