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
never sent a run. See `delivery_scheduler`.

Holding the lock on a connection of its own is the fix, and it has to be an
`AsyncConnection` rather than a `Session`: a connection checked out with
`scheduler_engine.connect()` stays checked out for the life of the block whether
or not it commits, so the lock and the unlock are guaranteed to be the same
connection.

**It is `scheduler_engine`, never the request `engine` (WP5, F-OPS-13).** Every
holder of one of these locks is a background loop, and one of them — the
aggregator scheduler leader — keeps its connection checked out for the entire
life of leadership (an `asyncio.gather` on child loops that never returns). On
the request `engine` that pinned a customer-pool connection forever; on the
scheduler pool it pins one of the loops' own three, which is exactly what that
pool is for.

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
from sqlalchemy.ext.asyncio import AsyncSession

# The SCHEDULER pool, bound to the module name `engine` every holder and every
# test already uses. Every advisory-lock holder is a background loop, and the
# aggregator leader holds its connection for the whole life of leadership — that
# belongs on the loops' own three connections, never a request's (F-OPS-13).
from app.core.database import scheduler_engine as engine

logger = logging.getLogger(__name__)

__all__ = ["held", "held_for_request"]

_ACQUIRE = text("SELECT pg_try_advisory_lock(:key)")
_ACQUIRE_WAIT = text("SELECT pg_advisory_lock(:key)")
_RELEASE = text("SELECT pg_advisory_unlock(:key)")
_ACQUIRE_XACT = text("SELECT pg_advisory_xact_lock(:key)")


@asynccontextmanager
async def held_for_request(db: AsyncSession, key: int) -> AsyncIterator[None]:
    """
    Serialise a check-then-write on the CALLER's request session, until it commits.

    The request-scoped sibling of `held`, and it exists because `held` is the
    wrong tool for a guard that reads committed rows. `held` takes a
    *session-level* lock on a **separate** scheduler connection and releases it
    the moment its `with` block exits — which is correct for a background sweep
    that commits its own work inside the block, and wrong for a request handler,
    where the row this guard just inserted is not committed until `get_db`
    commits, *after* the handler returns. A second racer would acquire `held`'s
    lock in that gap, count, and miss the first's still-uncommitted row — the
    exact double-booking the lock was meant to stop.

    `pg_advisory_xact_lock` is **transaction-scoped**: it is held on this
    session's own connection until the request's transaction commits or rolls
    back, so the whole check → insert → commit is one critical section. The
    blocking acquire (the analogue of `held(..., wait=True)`) serialises racers:
    the second waits for the first to commit, then counts the first's now-visible
    row and refuses.

    Nothing to release by hand — the transaction end does it — so this yields
    nothing and only marks the section. Use it for count-based capacity a single
    row lock cannot express (custom-order slots per day); reach for a plain
    `SELECT … FOR UPDATE` where there *is* a row to lock (a promo's ceiling).
    """
    await db.execute(_ACQUIRE_XACT, {"key": key})
    yield


@asynccontextmanager
async def held(key: int, *, name: str, wait: bool = False) -> AsyncIterator[bool]:
    """
    Yield whether this worker got the lock, and release it on the way out.

    `False` is an ordinary outcome, not a failure: it means another worker is
    already inside the sweep. Callers do nothing and wait for the next tick.

    With `wait=True` the acquire BLOCKS (`pg_advisory_lock`) until the lock is
    free instead of returning `False` — for a caller that must run its critical
    section rather than skip it (a manual re-promote the user asked for), and to
    serialise it with the try-lock holders so they don't deadlock on the same
    rows. It then always yields `True`.
    """
    async with engine.connect() as conn:
        if wait:
            await conn.execute(_ACQUIRE_WAIT, {"key": key})
            got = True
        else:
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
