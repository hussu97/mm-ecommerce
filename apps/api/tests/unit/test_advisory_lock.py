"""
The lock that decides which worker sweeps, and the way it used to leak.

Advisory locks in Postgres belong to a *connection*. Everything else in this app
talks to the database through `AsyncSessionFactory`, and a `Session` hands its
connection back to the pool every time it commits — so a lock taken on a session
and released on the same session is only released by luck. Quiet pool, same
connection back, unlock works. Busy pool, different connection back, unlock
returns false and the lock sits on the original connection until it is recycled.

Nothing raises when that happens. `pg_try_advisory_lock` is a try-lock, so every
sweep afterwards reads its `False` as "somebody else is on it" and returns
quietly, and the loop keeps ticking while nothing at all gets sent. That is what
happened on 2026-08-19: `Batch dispatcher started` in the log, a run three
minutes past its departure in the table, and no error anywhere.

These tests are about connection identity, because that is what the bug was.
"""

from __future__ import annotations

import pytest

from app.core import advisory_lock

KEY = 0x6D6D_4241_5443_4801


class _Conn:
    """One pooled connection, remembering what was run on it."""

    def __init__(self, tag: str, *, grants: bool):
        self.tag = tag
        self.grants = grants
        self.statements: list[str] = []
        self.commits = 0
        self.closed = False

    async def scalar(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        if "pg_try_advisory_lock" in sql:
            return self.grants
        if "pg_advisory_unlock" in sql:
            # Postgres answers false when this connection is not the holder,
            # which is precisely the symptom the old code could not see.
            return self.grants
        return None

    async def execute(self, stmt, params=None):
        # The blocking acquire (`pg_advisory_lock`) returns void, so it is run via
        # execute rather than scalar. It always "succeeds" (it waits in Postgres).
        self.statements.append(str(stmt))
        return None

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return False


class _Engine:
    """Hands out a fresh connection per `connect()`, and keeps every one."""

    def __init__(self, *, grants: bool = True):
        self.grants = grants
        self.handed_out: list[_Conn] = []

    def connect(self):
        conn = _Conn(f"conn-{len(self.handed_out)}", grants=self.grants)
        self.handed_out.append(conn)
        return conn


@pytest.mark.asyncio
async def test_the_lock_is_taken_and_returned_on_one_connection(monkeypatch):
    """
    The whole fix. Acquire and release must land on the same connection, so it
    has to be a connection the helper owns rather than one a session is
    borrowing between commits.
    """
    engine = _Engine()
    monkeypatch.setattr(advisory_lock, "engine", engine)

    async with advisory_lock.held(KEY, name="test") as mine:
        assert mine is True

    assert len(engine.handed_out) == 1, "the lock changed connections mid-flight"
    conn = engine.handed_out[0]
    assert any("pg_try_advisory_lock" in s for s in conn.statements)
    assert any("pg_advisory_unlock" in s for s in conn.statements)
    assert conn.closed


@pytest.mark.asyncio
async def test_the_lock_is_returned_even_when_the_work_raises(monkeypatch):
    """A sweep that blows up must not take the dispatcher down with it."""
    engine = _Engine()
    monkeypatch.setattr(advisory_lock, "engine", engine)

    with pytest.raises(RuntimeError):
        async with advisory_lock.held(KEY, name="test"):
            raise RuntimeError("courier exploded")

    assert any("pg_advisory_unlock" in s for s in engine.handed_out[0].statements), (
        "the lock would be stranded until the connection recycled"
    )


@pytest.mark.asyncio
async def test_the_connection_does_not_sit_in_a_transaction(monkeypatch):
    """
    Committed straight after the lock is taken. The work inside can take as long
    as a courier's API does, and an idle-in-transaction connection holds the
    database's xmin horizon behind it for all of it.
    """
    engine = _Engine()
    monkeypatch.setattr(advisory_lock, "engine", engine)

    async with advisory_lock.held(KEY, name="test"):
        assert engine.handed_out[0].commits == 1, "left idle in transaction"


@pytest.mark.asyncio
async def test_losing_the_race_releases_nothing(monkeypatch):
    """
    A worker that did not get the lock must not issue an unlock: the lock is
    somebody else's, and `pg_advisory_unlock` from a non-holder is a no-op that
    would only teach us to ignore its answer.
    """
    engine = _Engine(grants=False)
    monkeypatch.setattr(advisory_lock, "engine", engine)

    async with advisory_lock.held(KEY, name="test") as mine:
        assert mine is False

    assert not any("pg_advisory_unlock" in s for s in engine.handed_out[0].statements)


@pytest.mark.asyncio
async def test_wait_blocks_for_the_lock_and_always_holds(monkeypatch):
    """`wait=True` must ALWAYS acquire (blocking `pg_advisory_lock`, not the
    try-lock) and yield True — for a caller that must run its critical section, not
    skip it. Even a connection that would have refused a try-lock (`grants=False`)
    holds under wait, because the blocking acquire waits in Postgres rather than
    returning false."""
    engine = _Engine(grants=False)
    monkeypatch.setattr(advisory_lock, "engine", engine)

    async with advisory_lock.held(KEY, name="test", wait=True) as mine:
        assert mine is True

    conn = engine.handed_out[0]
    assert any("pg_advisory_lock" in s for s in conn.statements)
    assert not any("pg_try_advisory_lock" in s for s in conn.statements)
    # It still releases on the same connection on the way out.
    assert any("pg_advisory_unlock" in s for s in conn.statements)


def test_run_range_promote_and_reconcile_hold_the_sweep_locks():
    """A manual range run must promote/reconcile under the SAME advisory locks the
    scheduled sweeps hold, or it deadlocks against the keeta-push / hourly promote
    on the shared `aggregator_order` rows (2026-08-30). Guards the shape."""
    import inspect

    from app.services.aggregators import ingest

    source = inspect.getsource(ingest._run_range_channel)
    assert "advisory_lock.held(" in source
    assert "_PROMOTE_LOCK_KEY" in source
    assert "_RECONCILE_LOCK_KEY" in source
    assert "wait=True" in source


# ── the callers ───────────────────────────────────────────────────────────────


def test_no_sweep_takes_the_lock_through_a_session():
    """
    The regression guard, and the only kind that catches this cheaply.

    The bug is not visible in a sweep's behaviour — it behaves identically until
    the pool happens to hand back a different connection, which no unit test can
    provoke and production provokes within minutes. What *is* checkable is the
    shape: an advisory lock statement anywhere near `AsyncSessionFactory` is the
    mistake, whoever writes it next.
    """
    import inspect

    from app.services import log_retention
    from app.services.delivery import delivery_scheduler

    for module in (delivery_scheduler, log_retention):
        source = inspect.getsource(module)
        assert "pg_try_advisory_lock" not in source, (
            f"{module.__name__} takes the lock itself; it must go through "
            "app.core.advisory_lock, which owns a connection to hold it on"
        )
        assert "pg_advisory_unlock" not in source, (
            f"{module.__name__} releases the lock itself"
        )
        assert "advisory_lock.held" in source, (
            f"{module.__name__} no longer serialises its sweep at all"
        )
