from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

__all__ = [
    "AsyncSessionFactory",
    "SchedulerSessionFactory",
    "engine",
    "scheduler_engine",
]

# Pool sizing is bounded by Postgres `max_connections` (30 on the e2-small — see
# docker-compose.prod.yml). Both apps share this engine module; the numbers come
# from Settings so compose can give the register a smaller idle pool than the
# storefront. Defaults (5 / 8) match production before they were settings.
# Compose then sets pos-api to 2 / 3 so the till does not keep 13 connections
# warm for a handful of terminals. Peak on a routine deploy (api ≤13 + pos-api
# ≤5 + worker/psql/monitoring) still fits under 27 available slots.
#
# The exhaustion on 2026-08-30 was NOT too small a pool — it was the aggregator
# sweep PINNING connections idle-in-transaction across a 6-minute reauth wait
# (fixed in ingest by committing/rolling back before the wait). Raising the
# storefront overflow is burst headroom, not the fix. For a materially larger
# pool, resize the VM (e2-medium / 4GB) and raise Postgres `max_connections` +
# its memory cap together — the production deploy recreates only api/pos-api,
# so a `max_connections` change is a separate, deliberate postgres recreate.
#
# ── Two engines, one process (WP5, F-OPS-1) ──────────────────────────────────
# A background sweep that pins a connection — waiting on a courier's API, holding
# an advisory lock for the life of a leader — must never be able to starve a
# customer request of one. So the request path and the scheduler loops draw from
# SEPARATE pools:
#
#   * `engine` / `AsyncSessionFactory` — the request path only. `pool_timeout=3`
#     so a saturated request fails fast (paired with `PoolSaturationMiddleware`,
#     which sheds load with a 503 before routing) rather than piling up behind a
#     20s wait; `pool_use_lifo=True` so a burst reuses the few hottest
#     connections instead of fanning out across the whole pool and leaving them
#     all warm.
#   * `scheduler_engine` / `SchedulerSessionFactory` — every `run_forever` loop
#     and every `advisory_lock.held()` (including the aggregator leader lock,
#     which is checked out for the whole life of leadership — F-OPS-13). A tiny,
#     deliberately-bounded pool: a scheduler that leaks or wedges can exhaust
#     ITS pool and go quiet, but can no longer touch the request pool.
#
# Connection budget (Postgres max_connections=30, 3 reserved for a superuser, so
# 27 usable): api request 5+5=10, scheduler 5+1=6, pos-api request 2+3=5, the
# green slot's steady overlap during a cutover ~2, a migration ~1 → 24 ≤ 27. The
# arithmetic is asserted by `tests/unit/test_pool_budget.py` so a pool change
# that would overrun `max_connections` fails CI rather than the VM.
#
# 2026-09-07 reallocation: the real load is the background/aggregator loops, not
# the storefront or the register. Both the request `engine` and the scheduler
# `scheduler_engine` live in the SAME storefront `api` process, so capacity moved
# from one to the other leaves that container's footprint (and the whole cutover
# budget) unchanged. The scheduler was doubled (2+1=3 → 5+1=6) to clear the
# QueuePool timeouts flooding from the inventory source-event sweep, the
# delivery/batch scheduler, grubops, branch-hours and aggregator ingest — several
# of which still hold a connection across a third-party HTTP call each tick (the
# F-OPS-5 per-tick restructure is only partly landed), so 3 slots starved. The 3
# slots came off the storefront request overflow (8 → 5, max 10): a customer
# request path that has never needed 10 concurrent connections since the loops
# moved to their own engine (the two outages this file guards against were the
# loops sharing the request pool — that is fixed), and it stays a
# secret-tunable knob (DATABASE_MAX_OVERFLOW) that can be raised back in one
# redeploy if the storefront ever needs it. The durable fix for the loops is
# finishing the per-tick session discipline, not growing this pool.
#
# The scheduler pool is HARDCODED (not from Settings) on purpose: only the
# storefront `api` slot runs the loops, its size must not track the request
# pool a GitHub secret can raise, and pos-api/api-green create the engine but —
# because the pool is lazy — never open a connection on it (they run no loops).

#: Postgres kills any transaction of ours that sits IDLE this long, rolling it
#: back and dropping the connection.
#:
#: This is a backstop, not a tuning knob, which is why it is a constant and not a
#: setting. It exists because the same class of bug has now taken production down
#: twice: some code path opens a transaction, writes a row, then awaits something
#: external and never commits. On 2026-09-06 a Deliveroo token re-mint wrote
#: `aggregator_session` through a stashed, out-of-scope session; the transaction
#: stayed open for THREE HOURS, nine backends queued behind its row lock, the pool
#: saturated and the API started returning 503. Nothing reaped it, because
#: `idle_in_transaction_session_timeout` was 0.
#:
#: Sixty seconds is far longer than any honest transaction here (services flush and
#: the request-scoped `get_db` commits) and short enough that a future leak is a
#: blip rather than an outage. It can only ever abort a transaction that is doing
#: NOTHING — a query still running is not idle — so it cannot cut a slow report or
#: a long migration short. Deliberately paired with no `statement_timeout` and no
#: `lock_timeout`: both can abort work that is making progress, and neither is what
#: this failure needed.
_IDLE_IN_TRANSACTION_TIMEOUT_MS = "60000"


def _connect_args(url: str) -> dict[str, object]:
    """asyncpg-only connect args. `server_settings` is an asyncpg concept, so it is
    keyed off the driver in the URL rather than assumed — the dev default and the
    test suite must not blow up on a non-asyncpg URL."""
    if "asyncpg" not in url:
        return {}
    return {
        "server_settings": {
            "idle_in_transaction_session_timeout": _IDLE_IN_TRANSACTION_TIMEOUT_MS,
        }
    }


#: The scheduler pool. Small and fixed — the loops are few and each holds a
#: connection only briefly (a sweep) or exactly once (a leader lock). See the
#: budget note above; kept a module constant, not a Setting, so it cannot be
#: enlarged by the same secret that raises the request pool.
_SCHEDULER_POOL_SIZE = 5
_SCHEDULER_MAX_OVERFLOW = 1

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    # Fail fast rather than queue: `PoolSaturationMiddleware` sheds load with a
    # 503 before a request ever reaches routing, so a request that still gets
    # here under saturation should give up in seconds, not sit for 20.
    pool_timeout=3,
    pool_recycle=3600,
    # Reuse the hottest few connections under a burst instead of spreading the
    # load across the whole pool and keeping every slot warm.
    pool_use_lifo=True,
    connect_args=_connect_args(settings.DATABASE_URL),
)

#: The scheduler engine. Every background `run_forever` loop and every
#: `advisory_lock.held()` binds here, NEVER to `engine`, so a wedged sweep can
#: only ever exhaust these three connections and never a request's.
scheduler_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=_SCHEDULER_POOL_SIZE,
    max_overflow=_SCHEDULER_MAX_OVERFLOW,
    # A little more patience than the request path: a sweep waiting a few seconds
    # for one of its own connections is fine; there is no customer on the line.
    pool_timeout=5,
    pool_recycle=3600,
    connect_args=_connect_args(settings.DATABASE_URL),
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

#: Sessions for the background loops. Identical options to `AsyncSessionFactory`
#: — only the engine (and therefore the pool) differs.
SchedulerSessionFactory = async_sessionmaker(
    scheduler_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
