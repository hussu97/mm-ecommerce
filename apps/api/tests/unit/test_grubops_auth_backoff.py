"""GrubOps Cognito auth: single-flight + throttle cooldown (the 2026-09-01 outage).

The orders sweep runs every 30s; on a cold cache each tick calls token() → _login().
Before this fix, a login that Cognito throttled (TooManyRequests) never cached a token,
so every tick re-logged-in and kept the rate limit hot forever. These lock in the two
guards: concurrent callers coalesce behind one auth, and after a throttle we back off
instead of hammering.
"""

import asyncio

import pytest

from app.services.providers.grubops_provider import GrubOpsClient, GrubOpsError


class _Cfg:
    is_configured = True


def _client() -> GrubOpsClient:
    c = GrubOpsClient(config=None)
    c._config = _Cfg()  # type: ignore[assignment]
    return c


@pytest.mark.asyncio
async def test_throttle_sets_cooldown_and_stops_re_login():
    c = _client()
    calls = {"n": 0}

    async def throttled():
        calls["n"] += 1
        raise GrubOpsError('Cognito refused: {"__type":"TooManyRequestsException"}')

    c._login = throttled  # type: ignore[assignment]
    c._refresh = throttled  # type: ignore[assignment]

    for _ in range(4):
        with pytest.raises(GrubOpsError):
            await c.token()

    # Only the FIRST tick reached Cognito; the cooldown fast-failed the other three.
    assert calls["n"] == 1
    assert c._cooldown_until is not None


@pytest.mark.asyncio
async def test_single_flight_coalesces_concurrent_logins():
    c = _client()
    calls = {"n": 0}

    async def ok():
        calls["n"] += 1
        await asyncio.sleep(0.01)
        c._store({"IdToken": "tok", "ExpiresIn": 3600})

    c._login = ok  # type: ignore[assignment]

    tokens = await asyncio.gather(*[c.token() for _ in range(5)])
    # Five concurrent callers on a cold cache → exactly one Cognito login.
    assert calls["n"] == 1
    assert tokens == ["tok"] * 5


@pytest.mark.asyncio
async def test_success_clears_cooldown():
    c = _client()
    c._cooldown_until = None
    c._store({"IdToken": "t", "ExpiresIn": 3600})  # a live token clears the cooldown
    assert c._cooldown_until is None
    assert await c.token() == "t"  # served from cache, no auth call
