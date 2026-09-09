"""Careem's server-side refresh of the sliding `session` cookie.

Careem's Kong gateway gates on a `session` cookie with a 60-min sliding TTL and
renews it in the Set-Cookie of every authenticated response; the httpx transport
otherwise discards it, so it ages out ~hourly. `prepare_session` harvests and
persists the renewed cookie so the session self-renews without a headed re-login.
"""

from __future__ import annotations

import pytest

from app.models.aggregator import SESSION_LIVE, SESSION_NEEDS_BOOTSTRAP
from app.services.aggregators import session_store
from app.services.aggregators.session_store import LoadedSession
from app.services.providers import careem_provider


class _Resp:
    def __init__(self, status: int, cookies: dict | None = None):
        self.status_code = status
        self.cookies = cookies or {}


class _FakeStoreSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def commit(self):
        self.committed = True


def _session(status=SESSION_LIVE, session_cookie="old|1"):
    return LoadedSession(
        channel="careem",
        account_ref="",
        cookies={"session": session_cookie, "sid": "auth"},
        status=status,
    )


def _patch_persist(monkeypatch):
    captured: dict = {}

    async def fake_persist(
        db, channel, account_ref="", *, cookies, cookie_expires_at=None
    ):
        captured["channel"] = channel
        captured["cookies"] = cookies

    monkeypatch.setattr(session_store, "record_cookie_refresh", fake_persist)
    monkeypatch.setattr(
        "app.core.database.AsyncSessionFactory", lambda: _FakeStoreSession()
    )
    return captured


@pytest.mark.asyncio
async def test_prepare_session_harvests_and_persists_renewed_cookie(monkeypatch):
    client = careem_provider.CareemClient()
    sess = _session()

    async def fake_raw(session, method, url, **kw):
        assert url.endswith("/v2/admin/merchants/user/scope")
        return _Resp(200, {"session": "new|2"})

    monkeypatch.setattr(client, "request_raw", fake_raw)
    captured = _patch_persist(monkeypatch)

    out = await client.prepare_session(object(), sess)

    assert out.cookies["session"] == "new|2"  # in-memory updated for this run
    assert out.cookies["sid"] == "auth"  # other cookies preserved
    assert captured["channel"] == "careem"
    assert captured["cookies"]["session"] == "new|2"  # persisted


@pytest.mark.asyncio
async def test_prepare_session_leaves_session_untouched_on_401(monkeypatch):
    # An already-dead (>60-min) session 401s the keepalive GET; return it as-is so
    # the sweep escalates to the headed worker exactly as before — no persist.
    client = careem_provider.CareemClient()
    sess = _session()

    async def fake_raw(session, method, url, **kw):
        return _Resp(401)

    monkeypatch.setattr(client, "request_raw", fake_raw)
    captured = _patch_persist(monkeypatch)

    out = await client.prepare_session(object(), sess)

    assert out.cookies["session"] == "old|1"
    assert captured == {}


@pytest.mark.asyncio
async def test_prepare_session_skips_a_non_live_session(monkeypatch):
    # needs_bootstrap can't be refreshed server-side — don't even probe.
    client = careem_provider.CareemClient()
    sess = _session(status=SESSION_NEEDS_BOOTSTRAP)
    called = False

    async def fake_raw(*a, **k):
        nonlocal called
        called = True
        return _Resp(200, {"session": "new|2"})

    monkeypatch.setattr(client, "request_raw", fake_raw)

    out = await client.prepare_session(object(), sess)

    assert out is sess
    assert called is False


def test_careem_is_wired_into_the_hours_preparers():
    # The hourly hours push must refresh careem too, not just deliveroo/talabat.
    import inspect

    src = inspect.getsource(
        __import__(
            "app.services.aggregators.hours_writers", fromlist=["_load_session"]
        )._load_session
    )
    assert '"careem": careem_provider.provider.prepare_session' in src
