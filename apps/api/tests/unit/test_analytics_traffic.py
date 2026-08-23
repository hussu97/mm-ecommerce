"""
What the analytics dashboard is allowed to say about Umami.

Two things went wrong here for a long time and both were invisible, which is
the point of these tests.

The first: every failure returned zeros. A revoked key, a plan without API
access, a network that could not be reached and a genuinely quiet week were the
same four noughts on the owner's screen, and telling them apart meant reading
the API's reply by hand from the VM.

The second: the storefront tracks fifteen custom events — `add_to_cart`,
`begin_checkout`, `order_completed` and the rest of `apps/web/lib/analytics.ts`
— and nothing here ever read a single one of them. They could arrive in Umami
in perfect order and still be invisible everywhere the shop actually looks,
which is indistinguishable from not arriving at all.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.api.v1.analytics import umami as mod


def _response(status: int, payload) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://api.umami.is/v1/websites/x/stats"),
    )


_STATS = {
    "visitors": 40,
    "visits": 50,
    "pageviews": 300,
    "bounces": 20,
    "totaltime": 5000,
}
_PAGEVIEWS = {"pageviews": [{"x": "2026-08-01", "y": 12}]}
_PATHS = [{"x": "/en", "y": 200}, {"x": "/en/cart", "y": 30}]
_EVENTS = [
    {"x": "add_to_cart", "y": 12},
    {"x": "order_completed", "y": 3},
    {"x": "begin_checkout", "y": 7},
]


class _Client:
    """Stands in for httpx.AsyncClient, answering each GET from a queue."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.headers_seen: list[dict] = []
        self.urls: list[str] = []
        self.params: list[dict] = []

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, *, headers=None, params=None, **_kwargs):
        self.urls.append(url)
        self.headers_seen.append(headers or {})
        self.params.append(params or {})
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(mod.settings, "UMAMI_API_KEY", "key", raising=False)
    monkeypatch.setattr(mod.settings, "UMAMI_WEBSITE_ID", "site", raising=False)


def _ok_client() -> _Client:
    return _Client(
        [
            _response(200, _STATS),
            _response(200, _PAGEVIEWS),
            _response(200, _PATHS),
            _response(200, _EVENTS),
        ]
    )


async def _traffic(monkeypatch, client: _Client):
    monkeypatch.setattr(mod.httpx, "AsyncClient", client)
    return await mod.get_traffic(start_date=None, end_date=None, _admin=None)


# ── the events nobody was reading ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_events_come_back_commonest_first(monkeypatch):
    client = _ok_client()
    result = await _traffic(monkeypatch, client)

    assert [(e.name, e.count) for e in result.events] == [
        ("add_to_cart", 12),
        ("begin_checkout", 7),
        ("order_completed", 3),
    ]
    assert result.error is None
    assert result.configured is True


@pytest.mark.asyncio
async def test_the_events_metric_is_actually_asked_for(monkeypatch):
    client = _ok_client()
    await _traffic(monkeypatch, client)

    types = [p.get("type") for p in client.params]
    assert "event" in types, "nothing asked Umami for the custom events"
    assert "path" in types


# ── a rate is not a count ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bounce_rate_and_duration_are_per_session(monkeypatch):
    result = await _traffic(monkeypatch, _ok_client())

    # 20 bounces across 50 visits is 40%, not 20%. 5000 seconds across 50
    # visits averages 100, not 5000.
    assert result.bounce_rate == 40.0
    assert result.avg_duration == 100.0


@pytest.mark.asyncio
async def test_no_sessions_does_not_divide_by_zero(monkeypatch):
    client = _Client(
        [
            _response(200, {"visitors": 0, "visits": 0, "pageviews": 0}),
            _response(200, {"pageviews": []}),
            _response(200, []),
            _response(200, []),
        ]
    )
    result = await _traffic(monkeypatch, client)

    assert result.bounce_rate == 0.0
    assert result.avg_duration == 0.0
    assert result.events == []


# ── failures say what happened ────────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_a_refused_key_is_reported_rather_than_read_as_no_traffic(
    monkeypatch, status
):
    client = _Client(
        [
            _response(status, {"error": {"message": "Invalid API key."}}),
            _response(200, {"pageviews": []}),
            _response(200, []),
            _response(200, []),
        ]
    )
    result = await _traffic(monkeypatch, client)

    assert result.configured is True, (
        "the key is set — the problem is not configuration"
    )
    assert result.error is not None
    assert "UMAMI_API_KEY" in result.error
    assert result.visitors == 0


@pytest.mark.asyncio
async def test_an_unreachable_umami_is_reported(monkeypatch):
    class _Boom(_Client):
        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("no route to host")

    result = await _traffic(monkeypatch, _Boom([]))

    assert result.error is not None
    assert "Could not reach Umami" in result.error


@pytest.mark.asyncio
async def test_an_unconfigured_umami_is_not_an_error(monkeypatch):
    monkeypatch.setattr(mod.settings, "UMAMI_API_KEY", "", raising=False)

    result = await mod.get_traffic(start_date=None, end_date=None, _admin=None)

    assert result.configured is False
    assert result.error is None


# ── the key travels the way Umami Cloud documents ─────────────────────────────


@pytest.mark.asyncio
async def test_the_api_key_is_sent_as_umamis_own_header(monkeypatch):
    client = _ok_client()
    await _traffic(monkeypatch, client)

    assert all(h.get("x-umami-api-key") == "key" for h in client.headers_seen)
