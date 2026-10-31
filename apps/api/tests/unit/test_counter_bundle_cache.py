"""
`counter_bundle_cache` without a database: when an entry is reused, and when a
write retires it.

The session-event half (an ORM write → a bump after the outermost commit) runs
against Postgres in `tests/integration/test_counter_bundle_cache.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text, update

from app.models.payment_method import PaymentMethod
from app.models.pos_counter import PosConfigBundle
from app.models.product import Product
from app.services.pos import counter_bundle_cache as cache


def _entry(gen=(0, None), built_at=1000.0) -> cache.Entry:
    return cache.Entry(hash="h", payload={}, generation=gen, built_at=built_at)


def test_an_entry_is_reused_under_its_generation(monkeypatch):
    monkeypatch.setattr(cache.time, "monotonic", lambda: 1010.0)
    branch = uuid.uuid4()
    entry = _entry()
    cache.store(branch, entry)
    assert cache.lookup(branch, (0, None)) is entry
    assert cache.lookup(uuid.uuid4(), (0, None)) is None, "per branch"


@pytest.mark.parametrize("current", [(1, None), (0, 7)])
def test_a_moved_generation_misses(current, monkeypatch):
    monkeypatch.setattr(cache.time, "monotonic", lambda: 1010.0)
    branch = uuid.uuid4()
    cache.store(branch, _entry())
    assert cache.lookup(branch, current) is None


def test_the_ttl_ceiling_retires_an_entry_whatever_the_generation(monkeypatch):
    branch = uuid.uuid4()
    cache.store(branch, _entry(built_at=1000.0))
    monkeypatch.setattr(cache.time, "monotonic", lambda: 1000.0 + 299.0)
    assert cache.lookup(branch, (0, None)) is not None
    monkeypatch.setattr(cache.time, "monotonic", lambda: 1000.0 + cache.TTL_CEILING)
    assert cache.lookup(branch, (0, None)) is None


async def test_bump_moves_the_local_generation_and_tells_redis(monkeypatch):
    calls: list[str] = []

    async def incr(key):
        calls.append(key)
        return 1

    async def no_redis(_key):
        return None

    monkeypatch.setattr(cache, "cache_incr", incr)
    monkeypatch.setattr(cache, "cache_get", no_redis)
    before = await cache.generation()
    cache.bump()
    after = await cache.generation()
    assert after[0] == before[0] + 1
    await asyncio.sleep(0)  # let the tracked task run
    assert calls == [cache.REDIS_KEY]


async def test_the_generation_carries_the_shared_counter(monkeypatch):
    async def shared(_key):
        return 41

    monkeypatch.setattr(cache, "cache_get", shared)
    assert (await cache.generation())[1] == 41


class _State:
    """The slice of `ORMExecuteState` the listener reads."""

    def __init__(self, statement, *, kind):
        self.statement = statement
        self.is_update = kind == "update"
        self.is_delete = kind == "delete"
        self.is_insert = kind == "insert"
        self.session = SimpleNamespace(info={})


@pytest.mark.parametrize(
    ("statement", "kind", "dirty"),
    [
        (update(Product).values(base_price=1), "update", True),
        (PaymentMethod.__table__.delete(), "delete", True),
        (update(PosConfigBundle).values(engine_version=1), "update", False),
        (text("UPDATE products SET base_price = 1"), "update", False),
    ],
    ids=["orm-update", "core-delete", "other-table", "raw-sql-is-ttl-only"],
)
def test_bulk_statements_on_an_input_table_mark_the_session(statement, kind, dirty):
    state = _State(statement, kind=kind)
    cache._do_orm_execute(state)
    assert bool(state.session.info.get(cache._DIRTY)) is dirty
