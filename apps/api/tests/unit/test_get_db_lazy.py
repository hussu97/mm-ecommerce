"""
`get_db_lazy` must open a session only when the handler actually uses it (F-OPS-3).

The cached public routes answer most requests from Redis and never touch the
database. With the ordinary `get_db` they still checked a request-pool connection
out on every one of those hits — the fan-out that saturates the pool under a
storefront burst. This proves the lazy dependency opens NOTHING on a hit and
behaves like a real session on a miss.
"""

from __future__ import annotations

import pytest

from app.core import deps


class _FakeSession:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def execute(self, *_a, **_k):
        return "result"

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return False


@pytest.fixture
def fake_factory(monkeypatch):
    made: list[_FakeSession] = []

    def factory():
        s = _FakeSession()
        made.append(s)
        return s

    monkeypatch.setattr(deps, "AsyncSessionFactory", factory)
    return made


@pytest.mark.asyncio
async def test_a_cache_hit_opens_no_session(fake_factory):
    gen = deps.get_db_lazy()
    db = await gen.__anext__()
    # Handler returns without ever touching db (a Redis hit).
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert fake_factory == [], "a session was opened on a cache hit"
    assert db.opened is False


@pytest.mark.asyncio
async def test_first_use_opens_a_real_session_and_commits(fake_factory):
    gen = deps.get_db_lazy()
    db = await gen.__anext__()
    result = await db.execute("select 1")  # a cache miss uses db
    assert result == "result"
    assert db.opened is True
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
    assert len(fake_factory) == 1
    assert fake_factory[0].committed is True
    assert fake_factory[0].closed is True


@pytest.mark.asyncio
async def test_an_error_rolls_the_session_back(fake_factory):
    gen = deps.get_db_lazy()
    db = await gen.__anext__()
    await db.execute("select 1")
    with pytest.raises(RuntimeError):
        await gen.athrow(RuntimeError("handler blew up"))
    assert fake_factory[0].rolled_back is True
    assert fake_factory[0].committed is False
    assert fake_factory[0].closed is True
