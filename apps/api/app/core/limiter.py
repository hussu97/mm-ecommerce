from slowapi import Limiter

from app.core.config import settings
from app.core.request_ip import client_ip

__all__ = [
    "limiter",
    "rate_limit_key",
]


def rate_limit_key(request) -> str:
    """The bucket a request counts against: its real client IP.

    `slowapi`'s default `get_remote_address` reads `request.client.host`, which
    behind the single nginx proxy is nginx's own address — one global bucket for
    the entire internet, so eleven failed PIN attempts from anywhere locked every
    till and `/auth/login` was 5/min for the whole world. `client_ip` reads the
    rightmost `X-Forwarded-For` hop instead (see `app.core.request_ip`), which is
    the address nginx actually saw and the only one a caller cannot spoof.
    """
    return client_ip(request) or "unknown"


# `storage_uri=REDIS_URL` moves the counters off per-process memory and into
# Redis, so a limit is shared across the blue/green slots and survives a restart
# — in-process storage meant every deploy doubled every limit and every restart
# forgot it. Empty REDIS_URL (dev/tests) falls back to in-memory, slowapi's own
# default.
limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=settings.REDIS_URL or "memory://",
)
