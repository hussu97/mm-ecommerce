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
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_timeout=20,
    pool_recycle=3600,
)

AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
