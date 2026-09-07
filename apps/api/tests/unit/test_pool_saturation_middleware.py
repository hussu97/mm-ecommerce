"""
Admission control: a saturated request pool sheds with a fast 503 (WP5, F-OPS-1).

Piling requests up behind a full pool is how a brief spike became a multi-hour
503 storm. The middleware answers "come back in a moment" in microseconds,
without a DB session or a `pool_timeout` wait — and never sheds `/ping` or
`/health`, because a healthcheck that fails under load restarts the very
container that was coping.
"""

from __future__ import annotations

import pytest

from app.app_setup import PoolSaturationMiddleware


class _CapturingSend:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)


class _DownstreamCalled(Exception):
    pass


async def _boom_app(scope, receive, send):
    # The wrapped app must NOT be reached when we shed.
    raise _DownstreamCalled()


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(path: str) -> dict:
    return {"type": "http", "path": path, "headers": []}


@pytest.mark.asyncio
async def test_sheds_with_503_and_retry_after_when_saturated():
    mw = PoolSaturationMiddleware(_boom_app, checkedout=lambda: 12, threshold=12)
    send = _CapturingSend()
    # Downstream must never be called — that is the whole point of shedding early.
    await mw(_http_scope("/api/v1/products"), None, send)
    start = send.messages[0]
    assert start["status"] == 503
    headers = dict(start["headers"])
    assert headers[b"retry-after"] == b"2"
    assert headers[b"content-type"] == b"application/json"


@pytest.mark.asyncio
async def test_passes_through_when_below_threshold():
    mw = PoolSaturationMiddleware(_ok_app, checkedout=lambda: 11, threshold=12)
    send = _CapturingSend()
    await mw(_http_scope("/api/v1/products"), None, send)
    assert send.messages[0]["status"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ping", "/health", "/health/integrations"])
async def test_never_sheds_the_health_probes_even_when_saturated(path):
    mw = PoolSaturationMiddleware(_ok_app, checkedout=lambda: 999, threshold=12)
    send = _CapturingSend()
    # _ok_app runs (200); if it had shed we would see a 503 instead.
    await mw(_http_scope(path), None, send)
    assert send.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_a_stats_failure_never_sheds_traffic():
    def _explode():
        raise RuntimeError("pool introspection blew up")

    mw = PoolSaturationMiddleware(_ok_app, checkedout=_explode, threshold=12)
    send = _CapturingSend()
    await mw(_http_scope("/api/v1/products"), None, send)
    # Fail open: an unreadable pool must serve, not refuse.
    assert send.messages[0]["status"] == 200


@pytest.mark.asyncio
async def test_non_http_scopes_pass_straight_through():
    seen = {}

    async def _lifespan_app(scope, receive, send):
        seen["type"] = scope["type"]

    mw = PoolSaturationMiddleware(_lifespan_app, checkedout=lambda: 999, threshold=1)
    await mw({"type": "lifespan"}, None, _CapturingSend())
    assert seen["type"] == "lifespan"
