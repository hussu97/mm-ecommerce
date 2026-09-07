"""
The two engines' pools must fit under Postgres `max_connections` (WP5, F-OPS-1).

A wrong pool number here is not a style nit — it is the class of change that
CAUSED the two multi-hour outages this package exists to prevent. Postgres on the
e2-small runs `max_connections=30` and reserves 3 for a superuser, so 27 are
available to the app. The budget the audit fixed:

    api request pool     5 + 5 = 10   (this process's `engine`)
    scheduler pool       5 + 1 =  6   (this process's `scheduler_engine`)
    pos-api request pool 2 + 3 =  5   (the register, from compose)
    green slot overlap        ~  2    (steady during a blue/green cutover)
    a migration               ~  1
    ────────────────────────────────
    total                       24  ≤ 27

    2026-09-07: 3 slots moved from the storefront request overflow (8→5) to the
    scheduler pool (2+1=3 → 5+1=6). Both engines live in the same api process, so
    the total is unchanged — the loops, not the request path, are the real load.

The request/scheduler numbers are read from the live engines so a code change to
either pool is caught here; pos-api's pool and `max_connections` are read from
compose so a change on either side that breaks the arithmetic fails CI.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core import database

COMPOSE = pathlib.Path(__file__).parents[3].parent / "docker-compose.prod.yml"

#: Postgres reserves this many connections for a superuser (superuser_reserved).
_SUPERUSER_RESERVED = 3
#: Steady-state allowance for the green slot's brief overlap during a cutover
#: (its schedulers are off) plus a running migration. Documented, not measured —
#: the audit's headroom line.
_CUTOVER_AND_MIGRATION_RESERVE = 2 + 1


def _engine_max(engine) -> int:
    """pool_size + max_overflow for an async engine, from the live pool."""
    pool = engine.pool
    return int(pool.size()) + int(pool._max_overflow)


def test_the_request_pool_is_five_plus_five():
    # 5+5=10 since the 2026-09-07 reallocation (was 5+8): 3 overflow slots went
    # to the scheduler pool, which carries the real load. A GitHub secret can
    # raise DATABASE_MAX_OVERFLOW back if the storefront ever needs them.
    assert database.engine.pool.size() == 5
    assert database.engine.pool._max_overflow == 5
    assert _engine_max(database.engine) == 10


def test_the_scheduler_pool_is_five_plus_one_and_hardcoded():
    assert database.scheduler_engine.pool.size() == 5
    assert database.scheduler_engine.pool._max_overflow == 1
    assert _engine_max(database.scheduler_engine) == 6
    # Hardcoded, not tracking the request-pool settings a secret can raise.
    assert database._SCHEDULER_POOL_SIZE == 5
    assert database._SCHEDULER_MAX_OVERFLOW == 1


def test_the_request_pool_fails_fast_and_reuses_hot_connections():
    # pool_timeout small (fail fast; the middleware sheds before this) and LIFO
    # so a burst reuses the hottest few connections.
    assert database.engine.pool._timeout == 3
    assert database.engine.pool._pool.use_lifo is True
    # The scheduler pool is a touch more patient; nobody waits on it.
    assert database.scheduler_engine.pool._timeout == 5


yaml = pytest.importorskip("yaml", reason="pyyaml needed to read the compose file")
pytestmark = pytest.mark.skipif(not COMPOSE.exists(), reason=f"{COMPOSE} not found")


def _compose():
    return yaml.safe_load(COMPOSE.read_text())


def _pos_pool_max() -> int:
    env = _compose()["services"]["pos-api"]["environment"]
    return int(str(env["DATABASE_POOL_SIZE"])) + int(str(env["DATABASE_MAX_OVERFLOW"]))


def _max_connections() -> int:
    m = re.search(r"-c max_connections=(\d+)", COMPOSE.read_text())
    assert m, "max_connections not found in the postgres command"
    return int(m.group(1))


def test_the_register_pool_from_compose_is_five():
    assert _pos_pool_max() == 5


def test_the_total_budget_fits_under_max_connections():
    request = _engine_max(database.engine)  # 10
    scheduler = _engine_max(database.scheduler_engine)  # 6
    pos = _pos_pool_max()  # 5
    total = request + scheduler + pos + _CUTOVER_AND_MIGRATION_RESERVE
    available = _max_connections() - _SUPERUSER_RESERVED
    assert total == 24
    assert available == 27
    assert total <= available, (
        f"pool budget {total} exceeds the {available} usable Postgres "
        f"connections (max_connections {_max_connections()} - "
        f"{_SUPERUSER_RESERVED} superuser-reserved)"
    )
