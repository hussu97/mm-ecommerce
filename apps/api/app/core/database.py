from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

__all__ = [
    "AsyncSessionFactory",
    "engine",
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


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=20,
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
