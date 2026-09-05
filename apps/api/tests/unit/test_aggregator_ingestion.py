"""Unit coverage for the aggregator ingestion — the pure logic, no DB.

The DB paths run against Postgres in production; what is worth pinning here is
the logic that would go wrong silently: the session envelope, the Careem scope
flattening (asserted against the real console payload), the reconciliation
deltas, the request-fingerprint assembly, and the fail-closed auth on the one
write path.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.services.aggregators import crypto
from app.services.aggregators.ingest import (
    _dubai_range_window,
    _start_of_today_dubai,
    _sweep_window,
)
from app.services.aggregators.normalized import GRAIN_LINE
from app.services.aggregators.reconcile import _item_discrepancy
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.careem_provider import CareemClient

# ── daily window is calendar-aligned to "yesterday" (Dubai) ───────────────────


def test_start_of_today_dubai_is_dubai_local_midnight():
    """Dubai-AWARE midnight of today — so `.date()` is the Dubai date, not the UTC
    one (which would be the previous day for the 4h Dubai leads UTC)."""
    now = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)  # 23:00 Dubai on the 28th
    start = _start_of_today_dubai(now)
    assert start.date() == date(2026, 8, 28)
    assert start.hour == 0
    assert start.utcoffset() == timedelta(hours=4)  # Dubai, not UTC
    assert start.astimezone(timezone.utc) == datetime(
        2026, 8, 27, 20, 0, tzinfo=timezone.utc
    )


def test_one_day_lookback_is_exactly_yesterdays_dubai_date():
    """With until = end-of-yesterday and since = start-of-yesterday, the inclusive
    `.date()` filters every provider uses land on yesterday's Dubai date only."""
    now = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)
    today_start = _start_of_today_dubai(now)
    until = today_start - timedelta(microseconds=1)
    since = today_start - timedelta(days=1)
    assert since.date() == date(2026, 8, 27)  # yesterday
    assert until.date() == date(2026, 8, 27)  # not today — inclusive filters stay put


def test_dubai_range_window_is_inclusive_business_dates():
    """An explicit range maps to Dubai day boundaries so every provider's
    `since.date()`/`until.date()` filter covers from_date..to_date inclusive."""
    since, until = _dubai_range_window(date(2026, 8, 27), date(2026, 8, 28))
    assert since.date() == date(2026, 8, 27)
    assert since.hour == 0 and since.minute == 0
    assert until.date() == date(2026, 8, 28)
    assert until.hour == 23 and until.minute == 59
    assert since.utcoffset() == timedelta(hours=4)  # Dubai, not UTC


def test_dubai_range_window_single_day():
    since, until = _dubai_range_window(date(2026, 8, 28), date(2026, 8, 28))
    assert since.date() == until.date() == date(2026, 8, 28)
    assert since < until


def test_sweep_window_precedence_range_over_lookback():
    """Explicit from/to wins over lookback_days/hours; lookback_hours is rolling;
    else the Dubai-calendar lookback ending at the last instant of yesterday."""
    now = datetime(2026, 8, 28, 19, 0, tzinfo=timezone.utc)
    # explicit range
    s, u = _sweep_window(
        now,
        from_date=date(2026, 8, 20),
        to_date=date(2026, 8, 22),
        lookback_days=99,
        lookback_hours=99,
    )
    assert s.date() == date(2026, 8, 20) and u.date() == date(2026, 8, 22)
    # rolling hours (no calendar boundary)
    s, u = _sweep_window(
        now, from_date=None, to_date=None, lookback_days=None, lookback_hours=6
    )
    assert u == now and s == now - timedelta(hours=6)
    # calendar lookback default → yesterday
    s, u = _sweep_window(
        now, from_date=None, to_date=None, lookback_days=1, lookback_hours=None
    )
    assert s.date() == date(2026, 8, 27) and u.date() == date(2026, 8, 27)


@pytest.fixture
def encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_CONFIG_ENCRYPTION_KEY", key
    )
    return key


# ── crypto ───────────────────────────────────────────────────────────────────
def test_crypto_round_trips_a_session_blob(encryption_key):
    payload = {"accessToken": "abc.def.ghi", "_px3": "sensor-cookie", "n": 1}
    sealed = crypto.encrypt_json(payload)
    assert sealed is not None
    assert "accessToken" not in sealed  # actually encrypted, not just encoded
    assert crypto.decrypt_json(sealed) == payload


def test_crypto_passes_none_through(encryption_key):
    assert crypto.encrypt_json(None) is None
    assert crypto.decrypt_json(None) is None
    assert crypto.decrypt_json("") is None


# ── daily scheduler (wall-clock, catch-up, retry) ────────────────────────────
import asyncio  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.services.aggregators import ingest  # noqa: E402

_DXB = ZoneInfo("Asia/Dubai")


@pytest.fixture
def run_hour_23(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_RUN_HOUR_DXB", 23)


def test_next_run_is_always_the_coming_23h_dubai(run_hour_23):
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)  # 14:00 DXB
    nxt = ingest._next_run_at(now)
    assert nxt > now and nxt.astimezone(_DXB).hour == 23
    assert nxt.astimezone(_DXB).date() == now.astimezone(_DXB).date()


def test_after_the_slot_next_run_rolls_to_tomorrow(run_hour_23):
    now = datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc)  # 00:00 Aug 28 DXB
    assert ingest._next_run_at(now).astimezone(_DXB).day == 28
    assert ingest._last_due_at(now).astimezone(_DXB).day == 27  # today's 23:00 passed


def test_last_due_is_the_most_recent_slot_at_or_before_now(run_hour_23):
    now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)  # 14:00 DXB, before 23:00
    last = ingest._last_due_at(now)
    assert last <= now and last.astimezone(_DXB).hour == 23
    assert last.astimezone(_DXB).date() == (
        now.astimezone(_DXB).date() - timedelta(days=1)
    )


async def test_retry_stops_once_a_run_is_recorded(monkeypatch):
    """A pass that records a run row on the first try does not retry."""
    calls = {"daily": 0}

    async def fake_daily():
        calls["daily"] += 1
        return (1, 0)

    retried = {"n": 0}

    async def fake_retry(slot):
        retried["n"] += 1

    monkeypatch.setattr(ingest, "run_daily_once", fake_daily)
    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_slot_ran_since", lambda since: _async_true())
    monkeypatch.setattr(ingest, "_retry_failed_channels_this_slot", fake_retry)

    await ingest._run_daily_with_retry()
    assert calls["daily"] == 1
    assert retried["n"] == 1  # once the slot is recorded, per-channel retry runs


async def test_retry_exhausts_when_nothing_records(monkeypatch):
    """A pass that never records a run retries up to the attempt budget, then gives up."""
    calls = {"daily": 0, "sleeps": 0}

    async def fake_daily():
        calls["daily"] += 1
        return (0, 0)

    async def fake_sleep(_seconds):
        calls["sleeps"] += 1

    monkeypatch.setattr(ingest, "run_daily_once", fake_daily)
    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_slot_ran_since", lambda since: _async_false())
    monkeypatch.setattr(ingest.asyncio, "sleep", fake_sleep)

    await ingest._run_daily_with_retry()
    assert calls["daily"] == ingest._RETRY_ATTEMPTS
    assert calls["sleeps"] == ingest._RETRY_ATTEMPTS - 1  # no sleep after the last try


async def _async_true():
    return True


async def _async_false():
    return False


# ── scheduler leader election (only one API slot ticks) ───────────────────────


def _patch_child_loops(monkeypatch, started):
    """Replace the forever-loops with markers that run until cancelled."""

    async def fake_daily():
        started.append("daily")
        await asyncio.Event().wait()

    async def fake_rolling():
        started.append("rolling")
        await asyncio.Event().wait()

    async def fake_coverage():
        started.append("coverage")
        await asyncio.Event().wait()

    monkeypatch.setattr(ingest, "run_scheduler_forever", fake_daily)
    monkeypatch.setattr(ingest, "run_sales_refresh_scheduler_forever", fake_rolling)
    monkeypatch.setattr(
        ingest, "run_coverage_backfill_scheduler_forever", fake_coverage
    )


async def test_supervisor_runs_both_loops_when_it_wins_leadership(monkeypatch):
    """The slot that acquires the leader lock starts BOTH schedulers."""
    started: list[str] = []
    _patch_child_loops(monkeypatch, started)

    @asynccontextmanager
    async def always_leader(key, *, name, wait=False):
        yield True

    monkeypatch.setattr(ingest.advisory_lock, "held", always_leader)

    task = asyncio.create_task(ingest.run_aggregator_schedulers_forever())
    await asyncio.sleep(0.05)  # let it acquire and spawn the children
    assert set(started) == {"daily", "rolling", "coverage"}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervisor_stands_by_and_never_ticks_when_not_leader(monkeypatch):
    """A slot that loses the lock runs NEITHER loop — it only polls. This is what
    stops a stale blue/green slot from 401ing and re-flagging sessions."""
    started: list[str] = []
    _patch_child_loops(monkeypatch, started)

    @asynccontextmanager
    async def never_leader(key, *, name, wait=False):
        yield False

    monkeypatch.setattr(ingest.advisory_lock, "held", never_leader)

    polls = {"n": 0}

    async def stop_after_one_poll(_seconds):
        polls["n"] += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(ingest.asyncio, "sleep", stop_after_one_poll)

    with pytest.raises(asyncio.CancelledError):
        await ingest.run_aggregator_schedulers_forever()
    assert started == []  # never started a scheduler
    assert polls["n"] == 1  # it stood by and re-polled


async def test_supervisor_promotes_from_standby_to_leader(monkeypatch):
    """A standby that later wins the lock (the old leader died) starts the loops."""
    started: list[str] = []
    _patch_child_loops(monkeypatch, started)

    real_sleep = asyncio.sleep  # capture before patching to avoid recursion
    verdicts = iter([False, True])

    @asynccontextmanager
    async def standby_then_leader(key, *, name, wait=False):
        yield next(verdicts, True)

    async def quick_poll(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(ingest.advisory_lock, "held", standby_then_leader)
    monkeypatch.setattr(ingest.asyncio, "sleep", quick_poll)

    task = asyncio.create_task(ingest.run_aggregator_schedulers_forever())
    await real_sleep(0.05)
    assert set(started) == {"daily", "rolling", "coverage"}  # promoted and ticking

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_deliveroo_augments_session_from_db_not_the_stale_constant(monkeypatch):
    """The org + outlets come from the account and the branch map, so a session
    that carries neither resolves the live outlets (incl. one the constant lacks)
    rather than the hard-coded fallback."""
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers import deliveroo_provider as dp

    outlets_in_db = ["693359", "693360", "693361", "701111"]  # 701111 not in constant

    async def fake_scalars(_stmt):
        return outlets_in_db

    async def fake_load(_db, _channel):
        return SimpleNamespace(extras={"org_id": "497912"}, email="e", password="p")

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    fake_db = SimpleNamespace(scalars=fake_scalars)

    session = LoadedSession(
        channel=dp.CHANNEL_DELIVEROO,
        account_ref="",
        status=dp.SESSION_LIVE,
        tokens={},
    )
    out = await dp.provider._augment_from_db(fake_db, session)

    assert out.tokens["org_id"] == "497912"
    assert out.tokens["restaurant_ids"] == outlets_in_db
    # the sync getters now read the DB-sourced values off the session
    assert dp.provider._restaurant_ids(out) == outlets_in_db
    assert "701111" in dp.provider._restaurant_ids(out)  # the constant never had it


async def test_deliveroo_augment_raises_when_org_absent(monkeypatch):
    """No hard-coded org fallback: a session+account with no org_id raises loudly
    (retry-later) instead of scoping every request to a stale default that 401s."""
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers import deliveroo_provider as dp

    async def fake_load(_db, _ch):
        return SimpleNamespace(extras={}, email="e", password="p")  # no org_id

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)

    async def fake_outlets(_db):
        return ["693359"]

    monkeypatch.setattr(dp, "_outlet_ids_from_map", fake_outlets)

    session = LoadedSession(
        channel=dp.CHANNEL_DELIVEROO, account_ref="", status=dp.SESSION_LIVE, tokens={}
    )
    with pytest.raises(dp.AggregatorUnavailableError):
        await dp.provider._augment_from_db(SimpleNamespace(), session)


class _FakeDeliverooLogin:
    """A fake Partner Hub /api/session response for exercising `_login`."""

    def __init__(self, *, status=200, body=None):
        self._status = status
        self._body = body or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, *_a, **_k):
        return SimpleNamespace(status_code=self._status, json=lambda: self._body)


def _wire_login(monkeypatch, *, status, body):
    """Patch account_store, httpx, the outlet map, and session_store around
    `_login`, returning the dict of kwargs the mint upserts."""
    import httpx

    from app.services.aggregators import session_store
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers import deliveroo_provider as dp

    async def fake_account(_db, _ch):
        return SimpleNamespace(email="e", password="p", extras={"org_id": "497912"})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_account)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **k: _FakeDeliverooLogin(status=status, body=body),
    )

    async def fake_outlets(_db):
        return ["693359"]

    monkeypatch.setattr(dp, "_outlet_ids_from_map", fake_outlets)

    captured: dict = {}

    async def fake_upsert(_db, **kwargs):
        captured.update(kwargs)

    async def fake_reload(_db, _ch):
        return LoadedSession(
            channel=dp.CHANNEL_DELIVEROO,
            account_ref="",
            status=dp.SESSION_LIVE,
            tokens=captured.get("tokens", {}),
            cookies=captured.get("cookies", {}),
        )

    monkeypatch.setattr(session_store, "upsert_bootstrap", fake_upsert)
    monkeypatch.setattr(session_store, "load", fake_reload)
    return captured


async def test_deliveroo_login_keeps_account_org_and_carries_antibot_cookies(
    monkeypatch,
):
    """The mint must NOT overwrite the account org with restaurant_companies[0].id
    (a company id — the wrong scope that 401'd one second after 'login ok'), and it
    must carry the browser-captured cf_clearance cookie forward (the Cloudflare-
    fronted data endpoint needs it; the mint returns only the JWT)."""
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers import deliveroo_provider as dp

    captured = _wire_login(
        monkeypatch,
        status=200,
        body={
            "access_token": "fresh-token",
            "session_id": "s1",
            "restaurant_companies": [{"id": 999999}],  # company id — must NOT win
        },
    )
    previous = LoadedSession(
        channel=dp.CHANNEL_DELIVEROO,
        account_ref="",
        cookies={"cf_clearance": "cf-abc", "token": "old"},
        tokens={},
        header_profile={},
    )

    out = await dp.provider._login(SimpleNamespace(), previous)

    assert out is not None
    assert captured["tokens"]["org_id"] == "497912"  # account org, not 999999
    assert captured["cookies"]["cf_clearance"] == "cf-abc"  # carried forward
    assert captured["cookies"]["token"] == "fresh-token"  # fresh token overlaid


async def test_deliveroo_login_failure_returns_none_not_stale_session(monkeypatch):
    """A hard login failure returns None (→ reauth path flags the channel), NOT the
    stale previous session — proceeding on an expired Bearer is what 401'd forever."""
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers import deliveroo_provider as dp

    _wire_login(monkeypatch, status=401, body={})
    previous = LoadedSession(
        channel=dp.CHANNEL_DELIVEROO,
        account_ref="",
        status=dp.SESSION_LIVE,
        tokens={"access_token": "stale"},
        cookies={"token": "stale"},
    )

    out = await dp.provider._login(SimpleNamespace(), previous)
    assert out is None


def test_crypto_refuses_without_a_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_CONFIG_ENCRYPTION_KEY", "")
    assert crypto.is_configured() is False
    with pytest.raises(crypto.AggregatorCryptoError):
        crypto.encrypt_json({"x": 1})


def test_crypto_reports_a_rotated_key(encryption_key):
    sealed = crypto.encrypt_json({"x": 1})
    # A different key cannot open the blob — surfaced, never silently dropped.
    other = Fernet.generate_key().decode()
    import app.core.config as cfg

    cfg.settings.AGGREGATOR_CONFIG_ENCRYPTION_KEY = other
    with pytest.raises(crypto.AggregatorCryptoError):
        crypto.decrypt_json(sealed)


# ── Careem scope flattening (real console payload) ─────────────────────────────
_SCOPE = {
    "companies": [
        {
            "id": 1026653,
            "name": "Fatema Cake Sweets",
            "brands": [
                {
                    "id": 1029671,
                    "name": "Melting Moments - UAE",
                    "merchants": [
                        {"id": 1067984, "statusId": 1, "areaName": "Barsha Heights"},
                        {"id": 1069463, "statusId": 1, "areaName": "Silicon Oasis"},
                        {"id": 1087801, "statusId": 3, "areaName": "Al Majaz"},
                    ],
                }
            ],
        }
    ]
}


@pytest.fixture
def careem(monkeypatch):
    client = CareemClient()

    async def fake_request_json(self, session, method, url, **kwargs):
        return _SCOPE

    monkeypatch.setattr(CareemClient, "request_json", fake_request_json)
    return client


async def test_careem_flattens_scope_to_outlets(careem):
    outlets = await careem.discover_outlets(
        LoadedSession(channel="careem", account_ref="")
    )
    by_id = {o["external_outlet_id"]: o for o in outlets}
    assert set(by_id) == {"1067984", "1069463", "1087801"}
    # Barsha and DSO are active; Al Majaz (Sharjah) is statusId 3 → shut.
    assert by_id["1067984"]["active"] is True
    assert by_id["1069463"]["area_name"] == "Silicon Oasis"
    assert by_id["1087801"]["active"] is False
    # Every outlet carries the brand/company for the branch map.
    assert by_id["1067984"]["external_brand_id"] == "1029671"
    assert by_id["1067984"]["external_company_id"] == "1026653"


async def test_careem_billing_accounts_are_deduped_company_brand_merchant(careem):
    outlets = await careem.discover_outlets(
        LoadedSession(channel="careem", account_ref="")
    )
    accounts = careem._billing_accounts(outlets)
    types = sorted(a["billableType"] for a in accounts)
    # one COMPANY, one BRAND, three MERCHANTs — the brand/company deduped once.
    assert types == ["BRAND", "COMPANY", "MERCHANT", "MERCHANT", "MERCHANT"]


# ── Careem city id from account extras (vs the Dubai fallback) ─────────────────
def test_careem_city_id_falls_back_to_dubai_when_extras_absent():
    """An empty session yields the historical `1` (Dubai) — behaviour unchanged."""
    from app.services.providers import careem_provider as cp

    session = LoadedSession(channel="careem", account_ref="", tokens={})
    assert cp.CareemClient._city_id(session) == cp._DEFAULT_CITY_ID
    assert cp._DEFAULT_CITY_ID == "1"


def test_careem_city_id_reads_from_session_tokens():
    """A populated `city_id` (injected from account extras) wins over the default."""
    from app.services.providers import careem_provider as cp

    session = LoadedSession(channel="careem", account_ref="", tokens={"city_id": "9"})
    assert cp.CareemClient._city_id(session) == "9"


async def test_careem_orders_url_uses_the_session_city_id(monkeypatch):
    """The per-outlet orders URL carries the extras-sourced city, not a pinned 1."""
    from app.services.providers import careem_provider as cp

    client = cp.CareemClient()
    seen_urls: list[str] = []

    async def fake_request_json(self, session, method, url, **kwargs):
        if url.endswith("/user/scope"):
            return _SCOPE
        seen_urls.append(url)
        return {"orders": []}

    monkeypatch.setattr(cp.CareemClient, "request_json", fake_request_json)
    session = LoadedSession(channel="careem", account_ref="", tokens={"city_id": "9"})
    await client.fetch_sales(
        session, since=datetime(2026, 7, 1), until=datetime(2026, 7, 31)
    )
    assert seen_urls, "expected at least one per-outlet order fetch"
    assert all("/v1/careem/9/company/" in url for url in seen_urls)


async def test_careem_payout_pagination_stops_at_the_page_cap(monkeypatch):
    """A `totalRecords` that never satisfies the break is stopped by the hard cap."""
    from app.services.providers import careem_provider as cp

    client = cp.CareemClient()
    payout_pages = {"n": 0}

    async def fake_request_json(self, session, method, url, **kwargs):
        if url.endswith("/user/scope"):
            return _SCOPE
        # Always a full page and an unreachable total → only the cap can stop it.
        payout_pages["n"] += 1
        return {
            "payoutRequests": [{"id": f"p{payout_pages['n']}"}],
            "paginationInfo": {"totalRecords": 10**9},
        }

    monkeypatch.setattr(cp.CareemClient, "request_json", fake_request_json)
    result = await client.fetch_payouts(
        LoadedSession(channel="careem", account_ref=""),
        since=datetime(2026, 7, 1),
        until=datetime(2026, 7, 31),
    )
    assert payout_pages["n"] == cp._MAX_PAYOUT_PAGES
    assert len(result.payouts) == cp._MAX_PAYOUT_PAGES


# ── session enrichment from account extras (talabat entity, careem city) ──────
class _FakeScalarDB:
    """Minimal async db stub: `await db.scalars(...)` yields `rows`."""

    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, _stmt):
        return list(self._rows)


async def test_talabat_enrich_session_pulls_global_entity_from_account(monkeypatch):
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={"global_entity_id": "TB_KW"})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    # already carries store ids → the branch-map path is skipped
    session = ss.LoadedSession(
        channel="talabat", account_ref="", tokens={"account_ids": ["1"]}
    )
    out = await ss.enrich_session(_FakeScalarDB([]), session)
    assert out.tokens["global_entity_id"] == "TB_KW"


async def test_talabat_enrich_backfills_store_ids_from_branch_map(monkeypatch):
    """An automated re-login lands a session with no store ids; enrichment must
    inject the outlet ids from the branch map so `fetch_sales` can still scope."""
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    session = ss.LoadedSession(channel="talabat", account_ref="", tokens={})
    out = await ss.enrich_session(
        _FakeScalarDB(["793319", "711571", "711571"]), session
    )
    # de-duped, sorted, and byte-stable
    assert out.tokens["account_ids"] == ["711571", "793319"]


async def test_talabat_enrich_keeps_scraped_store_ids(monkeypatch):
    """A session that already scraped its store ids keeps them — the branch map
    only backfills, never overrides a captured session."""
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    session = ss.LoadedSession(
        channel="talabat", account_ref="", tokens={"account_ids": ["999"]}
    )
    out = await ss.enrich_session(_FakeScalarDB(["711571"]), session)
    assert out.tokens["account_ids"] == ["999"]


async def test_careem_enrich_session_pulls_city_id_from_account(monkeypatch):
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={"city_id": "9"})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    session = ss.LoadedSession(channel="careem", account_ref="", tokens={})
    out = await ss.enrich_session(SimpleNamespace(), session)
    assert out.tokens["city_id"] == "9"


# ── reconciliation item delta ─────────────────────────────────────────────────
def _agg_item(qty):
    return SimpleNamespace(grain=GRAIN_LINE, quantity=Decimal(str(qty)))


def _mm_item(qty, returned=0):
    return SimpleNamespace(effective_quantity=max(qty - returned, 0))


def test_item_discrepancy_matches_when_quantities_agree():
    detail, flagged = _item_discrepancy(
        [_agg_item(2), _agg_item(1)], [_mm_item(2), _mm_item(1)]
    )
    assert flagged is False
    assert detail["agg_total_qty"] == "3"
    assert detail["mm_total_qty"] == "3"


def test_item_discrepancy_flags_a_missing_item():
    # aggregator billed 3 units, the kitchen only made 2 (one returned).
    detail, flagged = _item_discrepancy(
        [_agg_item(2), _agg_item(1)], [_mm_item(2), _mm_item(1, returned=1)]
    )
    assert flagged is True


def test_item_discrepancy_is_unknown_for_aggregate_grain():
    detail, flagged = _item_discrepancy([], [_mm_item(2)])
    assert flagged is False
    assert "note" in detail


# ── request fingerprint assembly ──────────────────────────────────────────────
def test_build_headers_replays_profile_and_cookie():
    client = CareemClient()
    session = LoadedSession(
        channel="careem",
        account_ref="",
        cookies={"session-token": "abc", "_px3": "xyz"},
        header_profile={"User-Agent": "Chrome/151", "Application": "web"},
    )
    headers = client.build_headers(session, {"Authorization": "Bearer t"})
    assert headers["User-Agent"] == "Chrome/151"
    assert headers["Application"] == "web"
    assert headers["Authorization"] == "Bearer t"
    assert headers["Cookie"] == "session-token=abc; _px3=xyz"


def test_build_headers_fills_uae_chrome_when_the_profile_is_thin():
    from app.services.providers.aggregator_base import _ACCEPT_LANGUAGE, _CHROME_UA

    client = CareemClient()
    session = LoadedSession(channel="careem", account_ref="", cookies={"t": "1"})
    headers = client.build_headers(session)
    assert headers["User-Agent"] == _CHROME_UA
    assert headers["Accept-Language"] == _ACCEPT_LANGUAGE
    assert headers["Cookie"] == "t=1"


# ── session push auth (fail-closed) ───────────────────────────────────────────
async def test_session_push_rejected_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "")
    resp = await client.post(
        "/api/v1/aggregators/session",
        json={"channel": "careem"},
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


async def test_session_push_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.post(
        "/api/v1/aggregators/session",
        json={"channel": "careem"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_session_push_rejects_unknown_channel(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")
    resp = await client.post(
        "/api/v1/aggregators/session",
        json={"channel": "not-a-channel"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 400


async def test_worker_hydrate_rejected_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "")
    resp = await client.get(
        "/api/v1/aggregators/worker/sessions",
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


async def test_worker_hydrate_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.get(
        "/api/v1/aggregators/worker/sessions",
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_account_put_rejected_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "")
    resp = await client.put(
        "/api/v1/aggregators/account",
        json={"channel": "deliveroo", "email": "a@b.c", "password": "x"},
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


async def test_account_put_rejects_unknown_channel(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")
    resp = await client.put(
        "/api/v1/aggregators/account",
        json={"channel": "not-a-channel", "email": "a@b.c", "password": "x"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 400


async def test_worker_accounts_rejected_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "")
    resp = await client.get(
        "/api/v1/aggregators/worker/accounts",
        headers={"Authorization": "Bearer anything"},
    )
    assert resp.status_code == 401


def test_merge_credentials_keeps_password_when_omitted():
    from app.services.aggregators.account_store import merge_credentials

    merged = merge_credentials(
        {"email": "old@x", "password": "secret"},
        email="new@x",
        password=None,
    )
    assert merged["email"] == "new@x"
    assert merged["password"] == "secret"


def test_public_view_never_includes_the_password():
    from app.services.aggregators.account_store import LoadedAccount, public_view

    view = public_view(
        LoadedAccount(
            channel="deliveroo",
            login_method="email_password",
            email="h@x",
            password="must-not-leak",
            extras={"org_id": "497912"},
        )
    )
    assert "password" not in view
    assert view["has_password"] is True
    assert view["email"] == "h@x"
    assert view["login_method"] == "email_password"
    assert view["otp_required"] is False
    assert view["has_mailbox"] is False
    assert view["mailbox"] is None


def test_public_view_mailbox_never_includes_imap_password():
    from app.services.aggregators.account_store import LoadedAccount, public_view

    view = public_view(
        LoadedAccount(
            channel="talabat",
            login_method="email_password_otp",
            email="h@x",
            password="portal",
            mailbox={
                "host": "imap-mail.outlook.com",
                "port": 993,
                "username": "h@x",
                "password": "imap-secret",
                "folder": "INBOX",
            },
        )
    )
    assert view["otp_required"] is True
    assert view["has_mailbox"] is True
    assert view["mailbox"]["username"] == "h@x"
    assert "password" not in view["mailbox"]
    assert view["mailbox"]["has_password"] is True


def test_merge_mailbox_keeps_graph_secrets_when_omitted():
    from app.services.aggregators.account_store import (
        LoadedAccount,
        merge_mailbox,
        public_view,
    )

    merged = merge_mailbox(
        {
            "provider": "graph",
            "client_id": "app-talabat",
            "client_secret": "secret-talabat",
            "refresh_token": "rt-talabat",
            "tenant": "consumers",
        },
        {"provider": "graph", "client_id": "app-talabat", "client_secret": ""},
    )
    assert merged["client_secret"] == "secret-talabat"
    assert merged["refresh_token"] == "rt-talabat"

    view = public_view(
        LoadedAccount(
            channel="talabat",
            login_method="email_password_otp",
            email="h@x",
            mailbox=merged,
        )
    )
    assert view["has_mailbox"] is True
    assert view["mailbox"]["provider"] == "graph"
    assert view["mailbox"]["client_id"] == "app-talabat"
    assert view["mailbox"]["has_client_secret"] is True
    assert view["mailbox"]["has_refresh_token"] is True
    assert "client_secret" not in view["mailbox"]
    assert "refresh_token" not in view["mailbox"]


def test_merge_mailbox_graph_does_not_require_imap_host():
    from app.services.aggregators.account_store import merge_mailbox

    merged = merge_mailbox(
        None,
        {
            "provider": "graph",
            "client_id": "app-noon",
            "client_secret": "secret-noon",
            "tenant": "consumers",
        },
    )
    assert merged["client_id"] == "app-noon"
    assert "host" not in merged or not merged.get("host")


def test_merge_mailbox_keeps_password_when_omitted():
    from app.services.aggregators.account_store import merge_mailbox

    merged = merge_mailbox(
        {"host": "old.example", "password": "secret", "username": "a@b"},
        {"host": "imap-mail.outlook.com", "password": ""},
    )
    assert merged["host"] == "imap-mail.outlook.com"
    assert merged["password"] == "secret"


def test_merge_mailbox_clear_drops_the_row():
    from app.services.aggregators.account_store import merge_mailbox

    assert merge_mailbox({"host": "x", "password": "y"}, None, clear=True) is None


async def test_admin_accounts_require_permission(client):
    resp = await client.get("/api/v1/aggregators/accounts")
    assert resp.status_code in (401, 403)
    resp = await client.post(
        "/api/v1/aggregators/accounts",
        json={"channel": "deliveroo", "email": "a@b.c", "password": "x"},
    )
    assert resp.status_code in (401, 403)


def test_deliveroo_login_method_is_email_password():
    from app.models.aggregator import CHANNEL_LOGIN_METHODS, LOGIN_EMAIL_PASSWORD

    assert CHANNEL_LOGIN_METHODS["deliveroo"] == LOGIN_EMAIL_PASSWORD


def test_deliveroo_fils_money_is_aed_not_the_raw_integer():
    from decimal import Decimal

    from app.services.providers.deliveroo_provider import _fils

    assert _fils({"fractional": 4000, "formatted": "AED 40"}) == Decimal("40.00")
    assert _fils({"fractional": 71683}) == Decimal("716.83")
    assert _fils(None) is None


def test_deliveroo_list_order_uses_restaurant_id_as_outlet():
    from app.services.providers.deliveroo_provider import DeliverooClient

    order = DeliverooClient()._parse_list_order(
        {
            "order_number": "4332",
            "order_id": "7e9d337d-2981-36e9-a3b6-3b07f74c918e",
            "status": "delivered",
            "amount": {"fractional": 4000, "formatted": "AED 40"},
            "timeline": {"placed_at": "2026-08-26T17:48:25.328106+04:00"},
        },
        "693359",
    )
    assert order is not None
    assert order.external_order_id == "7e9d337d-2981-36e9-a3b6-3b07f74c918e"
    assert order.external_outlet_id == "693359"
    assert order.status == "delivered"
    assert order.gross_sales is not None
    assert str(order.gross_sales) == "40.00"
    assert order.business_date == "2026-08-26"


def test_deliveroo_headers_send_bearer_and_token_cookie():
    from app.services.aggregators.session_store import LoadedSession
    from app.services.providers.deliveroo_provider import DeliverooClient

    session = LoadedSession(
        channel="deliveroo",
        account_ref="",
        cookies={"token": "jwt-here"},
        tokens={"access_token": "jwt-here", "org_id": "497912"},
        header_profile={"user-agent": "Chrome/131"},
    )
    headers = DeliverooClient().build_headers(session)
    assert headers["Authorization"] == "Bearer jwt-here"
    assert "token=jwt-here" in headers["Cookie"]
    assert headers["X-Roo-Org-Id"] == "497912"


def test_noon_scope_merges_from_account_extras_without_overwriting_capture():
    from app.services.aggregators.session_store import (
        LoadedSession,
        merge_noon_scope_from_extras,
    )

    session = LoadedSession(
        channel="noon",
        account_ref="",
        cookies={"bm_sv": "x"},
        tokens={},
        header_profile={"user-agent": "Chrome/151"},
    )
    merged = merge_noon_scope_from_extras(
        session,
        {
            "restaurant_code": "R5967280642376629909871448A",
            "project": "PRJ135208",
            "locale": "en-ae",
        },
    )
    assert merged.tokens["restaurant_code"] == "R5967280642376629909871448A"
    assert merged.tokens["project"] == "PRJ135208"
    assert merged.header_profile["n-restaurantcode"] == "R5967280642376629909871448A"
    assert merged.header_profile["x-project"] == "PRJ135208"
    assert merged.header_profile["x-platform"] == "web"


def test_noon_wallet_json_lines_shape_parses():
    from app.services.providers.noon_provider import _parse_tabular

    body = (
        '{"status":"success","data":{"lines":[{"referenceNr":"ST1",'
        '"entryType":"statement","date":"2026-08-22","amount":10.5}]}}'
    )
    rows = _parse_tabular(body)
    assert len(rows) == 1
    assert rows[0]["referenceNr"] == "ST1"
    assert rows[0]["entryType"] == "statement"


def test_noon_publication_lookback_covers_weekly_statements():
    """A 1-day ingest since still discovers statements published ~a week earlier."""
    from datetime import date, datetime, timezone

    from app.core.config import settings
    from app.services.providers.noon_provider import (
        _in_window,
        _publication_since,
    )

    lookback = settings.AGGREGATOR_NOON_PUBLICATION_LOOKBACK_DAYS
    until = datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc)
    since = datetime(2026, 8, 26, 19, 0, tzinfo=timezone.utc)
    publish_since = _publication_since(since)
    assert (until.date() - publish_since.date()).days >= lookback - 1
    # Latest live statement was 2026-08-22 — outside 1-day, inside 14-day.
    assert not _in_window(date(2026, 8, 22), since, until)
    assert _in_window(date(2026, 8, 22), publish_since, until)


def test_talabat_csv_parses_orders_and_line_items():
    from app.services.providers.talabat_provider import TalabatClient

    csv_text = (
        "Order ID,Store ID,Order received at,Order status,Subtotal,Commission,"
        "Order Items\n"
        "TB123,711571,2026-08-26 14:30,Delivered,40.00,8.00,"
        '"2 Chocolate Cake"\n'
        "TB124,728173,2026-08-26 15:00,Cancelled,25.00,,"
        '"1 Red Velvet"\n'
    )
    orders = TalabatClient()._orders_from_csv(csv_text)
    assert len(orders) == 2
    assert orders[0].external_order_id == "TB123"
    assert orders[0].external_outlet_id == "711571"
    assert orders[0].status == "Delivered"
    assert len(orders[0].items) == 1
    assert orders[0].items[0].item_name == "Chocolate Cake"
    assert orders[0].items[0].quantity == Decimal("2")
    assert orders[0].items[0].amount_is_known is True
    assert orders[1].status == "Cancelled"


def test_registry_has_the_four_httpx_channels_and_not_keeta():
    from app.services.aggregators import ingest

    ingest.PROVIDERS.clear()
    ingest._register_providers()
    assert set(ingest.PROVIDERS) == {"careem", "deliveroo", "talabat", "noon"}
    # All four go out behind TLS impersonation — every partner API here is
    # Cloudflare/Akamai/PerimeterX fronted (Deliveroo's JSON tolerates httpx but
    # its invoice-file download needs the impersonated ClientHello).
    assert ingest.PROVIDERS["talabat"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["noon"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["careem"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["deliveroo"].uses_tls_impersonation is True


async def test_branch_map_admin_requires_permission(client):
    # No authenticated user → the catalogue.manage gate refuses the read.
    resp = await client.get("/api/v1/aggregators/branch-map")
    assert resp.status_code in (401, 403)


async def test_keeta_is_bootstrap_driven_not_httpx():
    from app.services.providers.aggregator_base import AggregatorUnavailableError
    from app.services.providers.keeta_provider import provider as keeta

    session = LoadedSession(channel="keeta", account_ref="")
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    with pytest.raises(AggregatorUnavailableError):
        await keeta.fetch_sales(session, since=now, until=now)


# ── Deliveroo modifier extraction ─────────────────────────────────────────────

_BASE_LIST_ROW = {
    "order_id": "ord-abc-123",
    "order_number": "4999",
    "status": "delivered",
    "amount": {"fractional": 6500},
    "placed_at": "2026-08-26T17:00:00+04:00",
}

_BASE_DETAIL: dict = {
    "id": "ord-abc-123",
    "status": "delivered",
    "amount": {"fractional": 6500},
    "items": [],
}


def _list_order():
    from app.services.providers.deliveroo_provider import DeliverooClient

    return DeliverooClient()._parse_list_order(_BASE_LIST_ROW, "693359")


def test_deliveroo_modifier_nested_groups_extracted_with_qty():
    """Nested modifier-group shape: item.modifiers → [{name, options: [...]}]"""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "items": [
            {
                "name": "Chocolate Cake",
                "quantity": 1,
                "total_price": {"fractional": 6500},
                "modifiers": [
                    {
                        "name": "Candles",
                        "options": [
                            {
                                "id": "opt-candle-3",
                                "name": "3 Candles",
                                "quantity": 2,
                                "price": {"fractional": 500},
                            }
                        ],
                    }
                ],
            }
        ],
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    assert len(order.items) == 1
    item = order.items[0]
    assert len(item.modifiers) == 1
    mod = item.modifiers[0]
    assert mod.name == "3 Candles"
    assert mod.quantity == Decimal("2")
    assert mod.external_ref == "opt-candle-3"
    assert item.modifiers_text is not None
    assert "3 Candles" in item.modifiers_text


def test_deliveroo_modifier_flat_options_extracted():
    """Flat shape: item.options = [{id, name, quantity, price}]"""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "items": [
            {
                "name": "Brownie Box",
                "quantity": 1,
                "total_price": {"fractional": 4000},
                "options": [
                    {"id": "opt-fudge", "name": "Fudge Sauce", "quantity": 1},
                    {"id": "opt-nuts", "name": "Nuts", "quantity": 2},
                ],
            }
        ],
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    item = order.items[0]
    assert len(item.modifiers) == 2
    names = {m.name for m in item.modifiers}
    assert names == {"Fudge Sauce", "Nuts"}
    nuts = next(m for m in item.modifiers if m.name == "Nuts")
    assert nuts.quantity == Decimal("2")
    assert nuts.external_ref == "opt-nuts"


def test_deliveroo_modifier_addons_fallback():
    """Fallback to item.addons when no modifiers/options key is present."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "items": [
            {
                "name": "Cupcakes",
                "quantity": 3,
                "total_price": {"fractional": 3000},
                "addons": [
                    {"id": "add-box", "name": "Gift Box", "quantity": 1},
                ],
            }
        ],
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    item = order.items[0]
    assert len(item.modifiers) == 1
    assert item.modifiers[0].name == "Gift Box"
    assert item.modifiers[0].quantity == Decimal("1")


def test_deliveroo_no_modifiers_gives_empty_list_and_no_text():
    """Items without any modifier/option key produce empty modifiers, no modifiers_text."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "items": [
            {
                "name": "Plain Cake",
                "quantity": 1,
                "total_price": {"fractional": 2500},
            }
        ],
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    item = order.items[0]
    assert item.modifiers == []
    assert item.modifiers_text is None


def test_deliveroo_timeline_fills_accepted_and_delivered():
    """Timeline keys on the detail populate accepted_at / delivered_at."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "timeline": {
            "placed_at": "2026-08-26T13:00:00+00:00",
            "accepted_at": "2026-08-26T13:02:30+00:00",
            "delivered_at": "2026-08-26T13:25:00+00:00",
        },
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    assert order.accepted_at is not None
    assert order.accepted_at.minute == 2
    assert order.delivered_at is not None
    assert order.delivered_at.minute == 25
    assert order.cancelled_at is None


def test_deliveroo_timeline_cancelled_at():
    """cancelled_at is filled when the timeline has a cancellation key."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "timeline": {
            "placed_at": "2026-08-26T13:00:00+00:00",
            "cancelled_at": "2026-08-26T13:01:00+00:00",
        },
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    assert order.cancelled_at is not None
    assert order.accepted_at is None
    assert order.delivered_at is None


def test_deliveroo_customer_from_nested_dict():
    """customer.name / customer.phone on the detail populate StandardOrder fields."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "customer": {"name": "Ahmed Al-Farsi", "phone": "+971501234567"},
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    assert order.customer_name == "Ahmed Al-Farsi"
    assert order.customer_phone == "+971501234567"


def test_deliveroo_customer_absent_stays_none():
    """No customer key on the detail → customer fields remain None."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    order = DeliverooClient()._merge_order_detail(_list_order(), _BASE_DETAIL, "693359")
    assert order.customer_name is None
    assert order.customer_phone is None


def test_deliveroo_multiple_modifier_groups_all_options_collected():
    """Multiple modifier groups → all options from all groups in one flat list."""
    from app.services.providers.deliveroo_provider import DeliverooClient

    detail = {
        **_BASE_DETAIL,
        "items": [
            {
                "name": "Cake Slice",
                "quantity": 1,
                "total_price": {"fractional": 3500},
                "modifiers": [
                    {
                        "name": "Flavour",
                        "options": [
                            {"id": "fl-choc", "name": "Chocolate", "quantity": 1}
                        ],
                    },
                    {
                        "name": "Topping",
                        "options": [
                            {"id": "tp-cream", "name": "Cream", "quantity": 1},
                            {"id": "tp-berry", "name": "Berries", "quantity": 2},
                        ],
                    },
                ],
            }
        ],
    }
    order = DeliverooClient()._merge_order_detail(_list_order(), detail, "693359")
    item = order.items[0]
    assert len(item.modifiers) == 3
    names = {m.name for m in item.modifiers}
    assert names == {"Chocolate", "Cream", "Berries"}
    berries = next(m for m in item.modifiers if m.name == "Berries")
    assert berries.quantity == Decimal("2")


# ── Deliveroo in-page finance ingest (DB mocked) ──────────────────────────────

_DELIVEROO_STATEMENT_CSV = (
    "Invoice Reference,55501\n"
    "Period,Aug 2026\n"
    "\n"
    "Restaurant Name,Order ID,Delivery Date & Time (UTC),Activity,Note,"
    "Order Value (د.إ),Adjustment Net (د.إ),"
    "Deliveroo Commission (د.إ),"
    "Commission / Adjustment VAT (د.إ),Total Payable\n"
    "My Restaurant,ORD-1,2026-08-10 10:00:00,,, 50.00,,  -5.00,  -0.25,  44.75\n"
    "My Restaurant,ORD-2,2026-08-11 12:30:00,,, 80.00,,  -8.00,  -0.40,  71.60\n"
)


@pytest.mark.asyncio
async def test_ingest_deliveroo_finance_payloads_parses_and_stamps_invoice():
    """A pushed invoice payload upserts one statement with CSV lines and an
    archived invoice key (store_statement_invoice mocked)."""
    import base64 as _b64
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.aggregators import ingest
    from app.services.aggregators.statement_docs import StoredStatementInvoice

    payload = {
        "invoice": {
            "id": "INV-500",
            "period_start": "2026-08-01",
            "period_end": "2026-08-31",
            "total": {"fractional": 11635},
            "currency": "AED",
        },
        "statement_csv": _DELIVEROO_STATEMENT_CSV,
        "statement_pdf_b64": _b64.b64encode(b"%PDF-1.4 fake").decode("ascii"),
    }

    stored = StoredStatementInvoice(
        object_key="invoices/deliveroo/INV-500/INV-500.pdf",
        content_type="application/pdf",
        original_filename="INV-500.pdf",
        fetched_at=None,
        size_bytes=13,
        attachments=None,
    )

    captured: dict = {}

    async def _capture_upsert(db, channel, statement):
        captured["channel"] = channel
        captured["statement"] = statement

    mock_db = MagicMock()
    with (
        patch(
            "app.services.providers.deliveroo_provider.store_statement_invoice",
            return_value=stored,
        ),
        patch.object(
            ingest, "_upsert_statement", new=AsyncMock(side_effect=_capture_upsert)
        ),
    ):
        statements, lines = await ingest.ingest_deliveroo_finance_payloads(
            mock_db, [payload]
        )

    assert statements == 1
    assert lines > 0
    assert captured["channel"] == "deliveroo"
    stmt = captured["statement"]
    assert stmt.statement_id == "INV-500"
    assert stmt.invoice_object_key == stored.object_key
    order_ids = {ln.external_order_id for ln in stmt.lines if ln.external_order_id}
    assert {"ORD-1", "ORD-2"} <= order_ids


@pytest.mark.asyncio
async def test_ingest_deliveroo_finance_payloads_skips_bad_payload():
    """A malformed payload is skipped, not fatal to the batch."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.aggregators import ingest

    good = {
        "invoice": {"id": "INV-1", "total": {"fractional": 0}, "currency": "AED"},
        "statement_csv": None,
        "statement_pdf_b64": None,
    }
    mock_db = MagicMock()
    with (
        patch(
            "app.services.providers.deliveroo_provider.store_statement_invoice",
            return_value=None,
        ),
        patch.object(ingest, "_upsert_statement", new=AsyncMock()),
    ):
        statements, lines = await ingest.ingest_deliveroo_finance_payloads(
            mock_db,
            ["not a dict", good],  # type: ignore[list-item]
        )

    assert statements == 1
    assert lines == 0


# ── placed_at timezone normalisation (the +4h "created after the sync" bug) ────
def test_aware_business_stamps_naive_dubai_wall_clock():
    """A naive marketplace timestamp (Talabat CSV, Keeta/Noon page JSON) is Dubai
    wall-clock. Stamp it with Dubai so its UTC instant is right — a naive value
    would land in the timestamptz column as UTC and read back +4h in the future."""
    naive = datetime(2026, 8, 26, 23, 16, 0)  # 23:16 Dubai
    out = ingest._aware_business(naive)
    assert out is not None
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(hours=4)
    # Same wall clock, and the correct instant: 23:16 Dubai == 19:16 UTC, not 23:16Z.
    assert out.replace(tzinfo=None) == naive
    assert out.astimezone(timezone.utc).hour == 19


def test_aware_business_leaves_aware_values_untouched():
    """Careem's REST offsets and Deliveroo's Z-suffixed ISO already arrive aware;
    the normaliser must not shift them."""
    aware = datetime(2026, 8, 26, 13, 0, 0, tzinfo=timezone.utc)
    assert ingest._aware_business(aware) is aware


def test_aware_business_passes_none_through():
    assert ingest._aware_business(None) is None


# ── manual run trigger: validation + daily/range dispatch ─────────────────────
async def test_trigger_no_dates_runs_the_daily_pass(monkeypatch):
    from app.api.v1.aggregators import trigger_sync_run

    seen = {}
    monkeypatch.setattr(
        ingest, "trigger_daily_in_background", lambda: seen.setdefault("daily", 1) == 1
    )
    monkeypatch.setattr(
        ingest,
        "trigger_range_in_background",
        lambda *a, **k: pytest.fail("range must not run without dates"),
    )

    async def fake_readiness(db, channels):
        return []

    monkeypatch.setattr(ingest, "session_readiness", fake_readiness)
    out = await trigger_sync_run(None, db=None)
    assert out.started is True
    assert "daily" in seen


async def test_trigger_with_both_dates_runs_a_range_backfill(monkeypatch):
    from app.api.v1.aggregators import trigger_sync_run
    from app.schemas.aggregator import AggregatorRunTriggerIn

    captured = {}

    def fake_range(from_date, to_date, channels):
        captured["args"] = (from_date, to_date, channels)
        return True

    monkeypatch.setattr(ingest, "trigger_range_in_background", fake_range)
    monkeypatch.setattr(
        ingest,
        "trigger_daily_in_background",
        lambda: pytest.fail("daily must not run when a range is given"),
    )

    async def fake_readiness(db, channels):
        return []

    monkeypatch.setattr(ingest, "session_readiness", fake_readiness)
    body = AggregatorRunTriggerIn(
        from_date=date(2026, 8, 27), to_date=date(2026, 8, 28)
    )
    out = await trigger_sync_run(body, db=None)
    assert out.started is True
    assert captured["args"] == (date(2026, 8, 27), date(2026, 8, 28), None)


async def test_trigger_surfaces_dead_sessions(monkeypatch):
    """A dead channel is reported on the trigger response (detail + session_health)
    so the operator sees it immediately, not after a failed run row."""
    from app.api.v1.aggregators import trigger_sync_run

    monkeypatch.setattr(ingest, "trigger_daily_in_background", lambda: True)

    async def fake_readiness(db, channels):
        return [
            {
                "channel": "noon",
                "status": "live",
                "usable": True,
                "reason": None,
                "token_expires_at": None,
                "cookie_expires_at": None,
            },
            {
                "channel": "careem",
                "status": "needs_bootstrap",
                "usable": False,
                "reason": "needs_bootstrap",
                "token_expires_at": None,
                "cookie_expires_at": None,
            },
        ]

    monkeypatch.setattr(ingest, "session_readiness", fake_readiness)
    out = await trigger_sync_run(None, db=None)
    assert out.started is True
    assert "careem" in out.detail and "not authenticated" in out.detail
    assert {r.channel: r.usable for r in out.session_health} == {
        "noon": True,
        "careem": False,
    }


async def test_trigger_requires_both_dates_or_neither():
    from app.api.v1.aggregators import trigger_sync_run
    from app.core.exceptions import BadRequestError
    from app.schemas.aggregator import AggregatorRunTriggerIn

    with pytest.raises(BadRequestError):
        await trigger_sync_run(AggregatorRunTriggerIn(from_date=date(2026, 8, 27)))


async def test_trigger_rejects_reversed_range():
    from app.api.v1.aggregators import trigger_sync_run
    from app.core.exceptions import BadRequestError
    from app.schemas.aggregator import AggregatorRunTriggerIn

    with pytest.raises(BadRequestError):
        await trigger_sync_run(
            AggregatorRunTriggerIn(
                from_date=date(2026, 8, 28), to_date=date(2026, 8, 27)
            )
        )


async def test_trigger_rejects_oversized_range():
    from app.api.v1.aggregators import trigger_sync_run
    from app.core.exceptions import BadRequestError
    from app.schemas.aggregator import AggregatorRunTriggerIn

    with pytest.raises(BadRequestError):
        await trigger_sync_run(
            AggregatorRunTriggerIn(
                from_date=date(2026, 1, 1), to_date=date(2026, 12, 31)
            )
        )


async def test_trigger_rejects_unknown_channel():
    from app.api.v1.aggregators import trigger_sync_run
    from app.core.exceptions import BadRequestError
    from app.schemas.aggregator import AggregatorRunTriggerIn

    with pytest.raises(BadRequestError):
        await trigger_sync_run(
            AggregatorRunTriggerIn(
                from_date=date(2026, 8, 27),
                to_date=date(2026, 8, 28),
                channels=["nope"],
            )
        )


async def test_trigger_503_when_ingest_disabled(monkeypatch):
    from app.api.v1.aggregators import trigger_sync_run
    from app.core.exceptions import ServiceUnavailableError

    monkeypatch.setattr(ingest, "trigger_daily_in_background", lambda: False)
    with pytest.raises(ServiceUnavailableError):
        await trigger_sync_run(None)


# ── push-only (Keeta) backfill via re-normalising stored raw ──────────────────
def test_push_order_parser_maps_keeta_only():
    from app.models.aggregator import CHANNEL_KEETA, CHANNEL_TALABAT

    assert ingest._push_order_parser(CHANNEL_KEETA) is not None
    assert ingest._push_order_parser(CHANNEL_TALABAT) is None


def test_keeta_order_from_raw_roundtrips_placed_at_to_correct_instant():
    """The public re-parse seam turns a saved payload back into a StandardOrder;
    its naive Dubai placed_at, normalised, is the real UTC instant — proving a
    Keeta backfill from `raw` corrects the +4h shift the same way a live pull does."""
    from app.services.providers import keeta_provider

    raw = {"orderId": "K-27", "orderTime": "2026-08-27T23:16:00+04:00"}
    order = keeta_provider.provider.order_from_raw(raw)
    assert order is not None
    assert order.external_order_id == "K-27"
    # Provider still yields naive Dubai wall-clock (unchanged contract)…
    assert order.placed_at == datetime(2026, 8, 27, 23, 16, 0)
    # …and the ingest normaliser turns that into the correct instant (19:16 UTC).
    aware = ingest._aware_business(order.placed_at)
    assert aware.utcoffset() == timedelta(hours=4)
    assert aware.astimezone(timezone.utc).hour == 19


async def test_renormalize_stored_reingests_each_row_from_raw(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.models.aggregator import CHANNEL_KEETA
    from app.services.aggregators.normalized import StandardOrder

    agg1 = SimpleNamespace(external_order_id="K1", raw={"id": "K1"})
    agg2 = SimpleNamespace(external_order_id="K2", raw={"id": "K2"})

    db = MagicMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [agg1, agg2]))

    # Deterministic parser so the test doesn't depend on Keeta's key vocabulary.
    monkeypatch.setattr(
        ingest,
        "_push_order_parser",
        lambda ch: lambda raw: StandardOrder(external_order_id=raw["id"]),
    )
    captured = []

    async def fake_upsert(_db, channel, order):
        captured.append((channel, order.external_order_id))

    monkeypatch.setattr(ingest, "upsert_order", fake_upsert)

    n = await ingest._renormalize_stored(
        db, CHANNEL_KEETA, date(2026, 8, 27), date(2026, 8, 28)
    )
    assert n == 2
    assert captured == [(CHANNEL_KEETA, "K1"), (CHANNEL_KEETA, "K2")]


async def test_renormalize_stored_noop_for_scraped_channel():
    from app.models.aggregator import CHANNEL_TALABAT

    # A scraped channel has no push parser, so nothing is re-ingested — it returns
    # before ever touching the db (so a bare sentinel stands in for the session).
    n = await ingest._renormalize_stored(
        object(), CHANNEL_TALABAT, date(2026, 8, 27), date(2026, 8, 28)
    )
    assert n == 0


# ── sales upsert isolation: a SAVEPOINT per order, systemic errors re-raised ──
class _FakeSavepoint:
    """Stands in for `AsyncSession.begin_nested()` — an async context manager that
    lets the exception propagate (returns False from __aexit__) so the caller's
    per-order try/except sees it, exactly as a real savepoint rollback would."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _sales_provider(order_ids):
    from unittest.mock import AsyncMock

    orders = [SimpleNamespace(external_order_id=oid) for oid in order_ids]
    result = SimpleNamespace(orders=orders, truncation_note=None)
    return SimpleNamespace(fetch_sales=AsyncMock(return_value=result))


async def test_fetch_and_persist_isolates_one_malformed_order(monkeypatch):
    """A single bad order must not lose the good ones. Before the savepoint fix a
    DB error poisoned the whole asyncpg transaction, so every later upsert failed
    "current transaction is aborted" and `written` collapsed to 0."""
    from unittest.mock import MagicMock

    db = MagicMock()
    db.begin_nested = lambda: _FakeSavepoint()

    async def fake_upsert(_db, _channel, order):
        if order.external_order_id == "BAD":
            raise ValueError("unparseable order")

    monkeypatch.setattr(ingest, "upsert_order", fake_upsert)

    written, _truncation, detail = await ingest._fetch_and_persist(
        db,
        "deliveroo",
        _sales_provider(["G1", "BAD", "G2"]),
        ingest.RUN_MODE_SALES,
        object(),
        since=datetime(2026, 8, 31, tzinfo=timezone.utc),
        until=datetime(2026, 8, 31, 23, tzinfo=timezone.utc),
    )
    assert written == 2  # both good orders persisted; the bad one was isolated
    assert detail == {"orders": 2}


async def test_fetch_and_persist_reraises_systemic_db_error(monkeypatch):
    """A schema/connection error is wrong for EVERY row (e.g. a migration not yet
    applied → UndefinedColumn). It must propagate so the run fails honestly, not
    be swallowed per-order and leave the run marked "completed" with 0 written."""
    from unittest.mock import MagicMock

    from sqlalchemy.exc import ProgrammingError

    db = MagicMock()
    db.begin_nested = lambda: _FakeSavepoint()

    async def fake_upsert(_db, _channel, _order):
        raise ProgrammingError(
            "INSERT ...", {}, Exception('column "marketing_fee" does not exist')
        )

    monkeypatch.setattr(ingest, "upsert_order", fake_upsert)

    with pytest.raises(ProgrammingError):
        await ingest._fetch_and_persist(
            db,
            "deliveroo",
            _sales_provider(["G1", "G2"]),
            ingest.RUN_MODE_SALES,
            object(),
            since=datetime(2026, 8, 31, tzinfo=timezone.utc),
            until=datetime(2026, 8, 31, 23, tzinfo=timezone.utc),
        )


# ── standing coverage backfill (multi-day gap recovery) ───────────────────────


def test_uncovered_dates_marks_days_with_orders_or_a_swept_backfill_as_covered():
    """A date is covered when an order landed on it OR a completed backfill span
    already swept it (inclusive); everything else in the window is a gap."""
    window = [date(2026, 9, d) for d in (1, 2, 3, 4)]
    # Orders on the 1st; a prior backfill swept 03..04 (even if it wrote nothing).
    gaps = ingest._uncovered_dates(
        window,
        order_dates={"2026-09-01"},
        swept_ranges=[("2026-09-03", "2026-09-04")],
    )
    assert gaps == [date(2026, 9, 2)]  # only the 2nd is neither


def test_uncovered_dates_empty_when_all_covered():
    window = [date(2026, 9, 1), date(2026, 9, 2)]
    assert (
        ingest._uncovered_dates(
            window, order_dates={"2026-09-01", "2026-09-02"}, swept_ranges=[]
        )
        == []
    )


class _FakeFactory:
    """A stand-in for AsyncSessionFactory: callable, and an async context manager
    yielding a throwaway db (the reads are monkeypatched, so the db is unused)."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_):
        return False


async def test_coverage_backfill_repulls_only_live_channels_with_gaps(monkeypatch):
    """A live channel with uncovered dates is re-pulled over the whole gap span
    (SALES only); a dead channel is skipped without waking its login; a live channel
    with no gaps is left alone."""
    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_register_providers", lambda: None)
    monkeypatch.setattr(
        ingest,
        "PROVIDERS",
        {"careem": object(), "deliveroo": object(), "noon": object()},
    )
    monkeypatch.setattr(ingest, "AsyncSessionFactory", _FakeFactory())
    monkeypatch.setattr(
        ingest.settings, "AGGREGATOR_COVERAGE_BACKFILL_DAYS", 7, raising=False
    )

    # careem is dead (skipped); deliveroo + noon are live.
    async def fake_session_for(_db, channel, _provider):
        return None if channel == "careem" else object()

    # deliveroo has a two-day gap; noon has none.
    gaps = {
        "deliveroo": [date(2026, 9, 2), date(2026, 9, 3)],
        "noon": [],
    }

    async def fake_gap_dates(_db, channel, _guard):
        return gaps[channel]

    calls: list[tuple] = []

    async def fake_run_range(channels, from_date, to_date, *, modes):
        calls.append((tuple(channels), from_date, to_date, modes))
        return []

    monkeypatch.setattr(ingest, "_session_for", fake_session_for)
    monkeypatch.setattr(ingest, "_coverage_gap_dates", fake_gap_dates)
    monkeypatch.setattr(ingest, "run_range", fake_run_range)

    backfilled = await ingest.run_coverage_backfill_once()

    assert backfilled == 1  # only deliveroo
    assert calls == [
        (("deliveroo",), date(2026, 9, 2), date(2026, 9, 3), (ingest.RUN_MODE_SALES,))
    ]


async def test_coverage_backfill_disabled_by_zero_guard(monkeypatch):
    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(
        ingest.settings, "AGGREGATOR_COVERAGE_BACKFILL_DAYS", 0, raising=False
    )

    async def boom(*_a, **_k):  # pragma: no cover — must never be reached
        raise AssertionError("should not scan providers when disabled")

    monkeypatch.setattr(ingest, "_register_providers", boom)
    assert await ingest.run_coverage_backfill_once() == 0


async def test_mm_order_for_external_matches_display_ref():
    """A short ticket still matches `display_ref` (Noon / GrubOps-style ids)."""
    captured = {}

    class _DB:
        async def scalar(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None

    out = await ingest._mm_order_for_external(_DB(), "deliveroo", "5254")
    assert out is None
    sql = captured["sql"]
    assert "display_ref" in sql
    assert "5254" in sql
    assert "external_order_id" in sql


def test_upsert_statement_links_orders_on_display_ref():
    import inspect

    src = inspect.getsource(ingest._order_matches_line_ids)
    assert "AggregatorOrder.display_ref.in_(ids)" in src
    assert '["drn_id"]' in src or "drn_id" in src


def test_deliveroo_settlement_joins_on_drn_id_not_short_or_order_number():
    """Live Deliveroo ids that must not be confused with each other.

    Sales list `order_id` is a v3 UUID; `order_number` / `display_ref` is the
    short ticket (9170); invoice CSV `Order Number` is a long numeric
    (51135384652) that the sales API never returns; CSV `Order ID` is the
    order-detail `drn_id` (a different v4 UUID). The join is that last one.
    """
    from sqlalchemy import select

    from app.models.aggregator import AggregatorOrder

    sales_uuid = "bd627d5f-d304-3a4f-92e4-34f92fbd4304"
    drn_uuid = "b9fa898d-83f7-44a6-a10a-71fe9f2cdbc5"
    short_ticket = "9170"
    csv_order_number = "51135384652"

    compiled = str(
        select(AggregatorOrder.id)
        .where(ingest._order_matches_line_ids({drn_uuid}))
        .compile(compile_kwargs={"literal_binds": True})
    )
    assert "drn_id" in compiled
    assert drn_uuid in compiled
    assert sales_uuid not in compiled
    assert short_ticket not in compiled
    assert csv_order_number not in compiled

    import inspect

    src = inspect.getsource(ingest._order_matches_line_ids)
    assert "external_order_id" in src
    assert "display_ref" in src
    assert "drn_id" in src


async def test_careem_empty_payouts_notes_channel_limit(monkeypatch):
    """An empty payoutRequests/list is the portal's answer, not a fetch bug."""
    from app.services.providers import careem_provider as cp

    client = cp.CareemClient()

    async def fake_request_json(self, session, method, url, **kwargs):
        if url.endswith("/user/scope"):
            return _SCOPE
        return {"payoutRequests": [], "paginationInfo": {"totalRecords": 0}}

    monkeypatch.setattr(cp.CareemClient, "request_json", fake_request_json)
    result = await client.fetch_payouts(
        LoadedSession(channel="careem", account_ref=""),
        since=datetime(2026, 7, 1),
        until=datetime(2026, 7, 31),
    )
    assert result.payouts == []
    assert result.truncation_note is not None
    assert "Tax Invoice" in result.truncation_note
