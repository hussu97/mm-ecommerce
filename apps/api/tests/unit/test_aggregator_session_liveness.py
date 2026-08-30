"""Proactive session liveness: a session is usable only when it is live AND not
past a stored token/cookie expiry. The stored expiries used to be written and
never read, so a predictably-dead session was only discovered by its 401 mid-run.
"""

from datetime import datetime, timedelta, timezone

from app.models.aggregator import (
    SESSION_DEAD,
    SESSION_LIVE,
    SESSION_NEEDS_BOOTSTRAP,
)
from app.services.aggregators import session_store
from app.services.aggregators.session_store import LoadedSession

_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _sess(**over) -> LoadedSession:
    base = dict(channel="noon", account_ref="", status=SESSION_LIVE)
    base.update(over)
    return LoadedSession(**base)


def test_live_session_with_no_expiry_is_usable():
    assert session_store.is_session_usable(_sess(), now=_NOW)
    assert session_store.session_unusable_reason(_sess(), now=_NOW) is None


def test_missing_session_is_not_usable():
    assert not session_store.is_session_usable(None, now=_NOW)
    assert "never bootstrapped" in session_store.session_unusable_reason(None, now=_NOW)


def test_needs_bootstrap_status_is_not_usable():
    s = _sess(status=SESSION_NEEDS_BOOTSTRAP)
    assert not session_store.is_session_usable(s, now=_NOW)
    assert session_store.session_unusable_reason(s, now=_NOW) == SESSION_NEEDS_BOOTSTRAP


def test_dead_status_is_not_usable():
    assert not session_store.is_session_usable(_sess(status=SESSION_DEAD), now=_NOW)


def test_expired_token_is_not_usable_even_when_status_live():
    s = _sess(token_expires_at=_NOW - timedelta(hours=1))
    assert not session_store.is_session_usable(s, now=_NOW)
    assert session_store.session_unusable_reason(s, now=_NOW) == "token expired"


def test_expired_cookie_is_not_usable():
    s = _sess(cookie_expires_at=_NOW - timedelta(minutes=1))
    assert session_store.session_unusable_reason(s, now=_NOW) == "cookie expired"


def test_talabat_expired_cookie_is_advisory_and_still_usable():
    """Talabat's PerimeterX cookie has a ~5-minute nominal TTL but rotates on
    replay, so its expiry must NOT reject the session — that starved every intraday
    sweep. Only Talabat is exempt; the same expired cookie on Noon is still fatal."""
    talabat = _sess(channel="talabat", cookie_expires_at=_NOW - timedelta(minutes=1))
    assert session_store.session_unusable_reason(talabat, now=_NOW) is None
    assert session_store.is_session_usable(talabat, now=_NOW)
    noon = _sess(channel="noon", cookie_expires_at=_NOW - timedelta(minutes=1))
    assert session_store.session_unusable_reason(noon, now=_NOW) == "cookie expired"


def test_talabat_expired_token_is_still_honoured():
    """Only the rotating cookie is advisory for Talabat — a real token expiry (the
    OTP session) still makes it unusable so the sweep reauths it."""
    s = _sess(channel="talabat", token_expires_at=_NOW - timedelta(hours=1))
    assert session_store.session_unusable_reason(s, now=_NOW) == "token expired"


def test_future_expiry_is_usable():
    s = _sess(
        token_expires_at=_NOW + timedelta(hours=2),
        cookie_expires_at=_NOW + timedelta(days=1),
    )
    assert session_store.is_session_usable(s, now=_NOW)


def test_naive_expiry_is_treated_as_utc():
    # A stored expiry written without tzinfo must still compare correctly.
    s = _sess(token_expires_at=(_NOW - timedelta(hours=1)).replace(tzinfo=None))
    assert not session_store.is_session_usable(s, now=_NOW)


# ── trigger-based reauth (the ingest side of the daemon handshake) ─────────────


class _FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


async def test_await_reauth_waits_then_returns_healed_session(monkeypatch):
    """The ingest pass flags the session and polls until the reauth daemon (in the
    worker) has brought it back live, then returns the fresh session to retry."""
    from app.services.aggregators import ingest

    calls = {"n": 0}
    healed = object()

    async def fake_session_for(db, channel, provider):
        calls["n"] += 1
        return healed if calls["n"] >= 2 else None  # daemon heals by the 2nd poll

    async def fake_load(db, channel):
        return None

    async def fake_mark(db, channel, *, error):
        return None

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(ingest, "_session_for", fake_session_for)
    monkeypatch.setattr(ingest.session_store, "load", fake_load)
    monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", fake_mark)
    monkeypatch.setattr(ingest.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ingest, "AsyncSessionFactory", lambda: _FakeDB())
    monkeypatch.setattr(ingest.settings, "AGGREGATOR_REAUTH_WAIT_SECONDS", 60)
    monkeypatch.setattr(ingest.settings, "AGGREGATOR_REAUTH_POLL_SECONDS", 1)

    out = await ingest._await_reauth("careem", provider=object())
    assert out is healed


async def test_await_reauth_disabled_returns_none(monkeypatch):
    """Wait disabled (0s) → flag-and-skip, the old behaviour."""
    from app.services.aggregators import ingest

    monkeypatch.setattr(ingest.settings, "AGGREGATOR_REAUTH_WAIT_SECONDS", 0)
    out = await ingest._await_reauth("careem", provider=object())
    assert out is None


async def test_await_reauth_times_out_when_daemon_never_heals(monkeypatch):
    """If the daemon never brings the session back within the window, return None
    (the channel is then skipped for this pass, not hung forever)."""
    from app.services.aggregators import ingest

    async def never(db, channel, provider):
        return None

    async def fake_load(db, channel):
        return None

    async def fake_mark(db, channel, *, error):
        return None

    async def fake_sleep(_s):
        return None

    monkeypatch.setattr(ingest, "_session_for", never)
    monkeypatch.setattr(ingest.session_store, "load", fake_load)
    monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", fake_mark)
    monkeypatch.setattr(ingest.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ingest, "AsyncSessionFactory", lambda: _FakeDB())
    # Tiny window so the wall-clock deadline passes immediately.
    monkeypatch.setattr(ingest.settings, "AGGREGATOR_REAUTH_WAIT_SECONDS", 1)
    monkeypatch.setattr(ingest.settings, "AGGREGATOR_REAUTH_POLL_SECONDS", 1)

    out = await ingest._await_reauth("careem", provider=object())
    assert out is None
