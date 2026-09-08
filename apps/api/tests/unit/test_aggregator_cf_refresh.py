"""Careem's Cloudflare edge-cookie refresh on a 401.

Careem sits behind Cloudflare; its ~1h edge cookie (`__cf_bm`/`cf_clearance`)
rotates and, replayed stale, the edge answers a bare 401 that looks like a dead
session. The transport warms a fresh cookie inside the same impersonated session
and retries once before giving up — turning an hourly headed re-login into a
sub-second refresh. These pin that recovery without a real network call.
"""

from __future__ import annotations

import pytest

from app.services.aggregators.session_store import LoadedSession
from app.services.providers import aggregator_base
from app.services.providers.aggregator_base import (
    AggregatorAuthError,
    BaseAggregatorClient,
)


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self.text = ""
        self._body = body or {"ok": True}

    def json(self):
        return self._body


class _FakeCurlSession:
    """Stands in for `curl_cffi.requests.AsyncSession`.

    `request` walks a scripted list of responses; `get` (the warm-up) optionally
    drops a fresh Cloudflare cookie into the jar, mirroring an edge that re-issues
    `__cf_bm` on a plain GET.
    """

    def __init__(self, script: list[_Resp], *, warmup_sets_cookie: bool):
        self._script = script
        self._warmup_sets_cookie = warmup_sets_cookie
        self.cookies: dict[str, str] = {}
        self.requests: list[dict] = []
        self.gets: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def request(self, method, url, **kw):
        self.requests.append(kw)
        return self._script.pop(0)

    async def get(self, url, **kw):
        self.gets.append((url, kw))
        if self._warmup_sets_cookie:
            self.cookies["__cf_bm"] = "fresh-bm"
        return _Resp(200)


def _client(cf_url: str | None):
    class _C(BaseAggregatorClient):
        channel = "careem"
        uses_tls_impersonation = True

        def _cf_refresh_url(self):
            return cf_url

        async def fetch_sales(self, *a, **k):  # pragma: no cover - unused
            raise NotImplementedError

        async def fetch_statements(self, *a, **k):  # pragma: no cover - unused
            raise NotImplementedError

        async def fetch_payouts(self, *a, **k):  # pragma: no cover - unused
            raise NotImplementedError

    return _C()


def _session():
    return LoadedSession(
        channel="careem",
        account_ref="",
        cookies={"__cf_bm": "stale-bm", "sid": "auth-token"},
    )


def _install(monkeypatch, fake: _FakeCurlSession):
    monkeypatch.setattr(aggregator_base, "_HAS_CURL_CFFI", True)
    monkeypatch.setattr(
        aggregator_base.curl_requests, "AsyncSession", lambda: fake, raising=False
    )


@pytest.mark.asyncio
async def test_cf_refresh_recovers_a_401(monkeypatch):
    fake = _FakeCurlSession([_Resp(401), _Resp(200)], warmup_sets_cookie=True)
    _install(monkeypatch, fake)

    client = _client("https://partners.careem.com/")
    out = await client.request_json(_session(), "GET", "https://partners.careem.com/x")

    assert out == {"ok": True}
    assert fake.gets, "the warm-up GET should have fired"
    # The retry drops the stale edge cookie so the jar's fresh one wins; the auth
    # cookie is kept.
    retry_cookies = fake.requests[1]["cookies"]
    assert "__cf_bm" not in retry_cookies
    assert retry_cookies["sid"] == "auth-token"


@pytest.mark.asyncio
async def test_no_fresh_cookie_falls_back_to_the_401(monkeypatch):
    # A real challenge (no fresh cookie issued) must still surface as a dead
    # session so the headed worker re-bootstraps.
    fake = _FakeCurlSession([_Resp(401)], warmup_sets_cookie=False)
    _install(monkeypatch, fake)

    client = _client("https://partners.careem.com/")
    with pytest.raises(AggregatorAuthError):
        await client.request_json(_session(), "GET", "https://partners.careem.com/x")


@pytest.mark.asyncio
async def test_non_cloudflare_channel_does_not_warm_up(monkeypatch):
    # A channel that returns no refresh URL keeps the old behaviour: a 401 is a
    # dead session, no warm-up attempted.
    fake = _FakeCurlSession([_Resp(401)], warmup_sets_cookie=True)
    _install(monkeypatch, fake)

    client = _client(None)
    with pytest.raises(AggregatorAuthError):
        await client.request_json(_session(), "GET", "https://x/y")
    assert fake.gets == []
