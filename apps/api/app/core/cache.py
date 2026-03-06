from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger("mm.cache")

_redis: Any = None


async def _get_redis() -> Any:
    global _redis
    if _redis is None and settings.REDIS_URL:
        try:
            import redis.asyncio as aioredis

            _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            # Verify connection
            await _redis.ping()
        except Exception as e:
            logger.warning("Redis unavailable, caching disabled: %s", e)
            _redis = None
    return _redis


async def cache_get(key: str) -> Any | None:
    r = await _get_redis()
    if not r:
        return None
    try:
        value = await r.get(key)
        return json.loads(value) if value is not None else None
    except Exception as e:
        logger.warning("Cache get error for key=%s: %s", key, e)
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    r = await _get_redis()
    if not r:
        return
    try:
        await r.setex(key, ttl, json.dumps(value, default=str))
    except Exception as e:
        logger.warning("Cache set error for key=%s: %s", key, e)


async def cache_delete(key: str) -> None:
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(key)
    except Exception as e:
        logger.warning("Cache delete error for key=%s: %s", key, e)


async def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching a glob pattern. Use sparingly."""
    r = await _get_redis()
    if not r:
        return
    try:
        keys = await r.keys(pattern)
        if keys:
            await r.delete(*keys)
    except Exception as e:
        logger.warning("Cache delete pattern error for pattern=%s: %s", pattern, e)
