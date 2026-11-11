"""App code reaching the database from consecutive tests must not share a pool.

Each async test runs on its own event loop, and an asyncpg connection belongs to
the loop that opened it. `app.core.database`'s engines are module-level, so
without `fresh_app_engine_pools` (tests/conftest.py) the second of these tests
checks out the connection the first one returned and fails with
`RuntimeError: ... attached to a different loop`. In the full suite that showed
up as an intermittent failure in whichever test happened to run next on the
same process — a flake that moved with the test ordering.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")),
    reason="needs a database — set TEST_DATABASE_URL",
)

_KEY = 7_341_001


async def _take_the_lock() -> None:
    from app.core import advisory_lock

    async with advisory_lock.held(_KEY, name="engine-across-loops test") as mine:
        assert mine


async def test_first_use_of_the_scheduler_pool():
    await _take_the_lock()


async def test_second_use_does_not_inherit_the_first_loops_connection():
    await _take_the_lock()
