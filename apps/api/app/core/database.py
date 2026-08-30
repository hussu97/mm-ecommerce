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
# docker-compose.prod.yml), which this ONE engine config serves for BOTH the
# storefront API and pos-api, plus a few worker/psql/monitoring connections and
# the brief doubling of a rolling deploy. `pool_size` is the kept-alive baseline;
# it is deliberately LEFT at 5 so the deploy-overlap baseline (5 per container ×
# the pair being recreated) is unchanged and safe under 30. Only `max_overflow`
# is raised (5 -> 8): burst headroom for the aggregator sweeps and request spikes,
# opened on demand and returned promptly, so the realistic peak (api up to 13 +
# pos-api's handful + overhead) still fits under 30.
#
# The exhaustion on 2026-08-30 was NOT too small a pool — it was the aggregator
# sweep PINNING connections idle-in-transaction across a 6-minute reauth wait
# (fixed in ingest by committing/rolling back before the wait). This is burst
# headroom, not the fix. For a materially larger pool, resize the VM (e2-medium /
# 4GB) and raise Postgres `max_connections` + its memory cap together — the
# production deploy recreates only api/pos-api, so a `max_connections` change is a
# separate, deliberate postgres recreate, not part of a routine deploy.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=8,
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
