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
from app.services.aggregators.ingest import _start_of_today_dubai
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

    monkeypatch.setattr(ingest, "run_daily_once", fake_daily)
    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_slot_ran_since", lambda since: _async_true())

    await ingest._run_daily_with_retry()
    assert calls["daily"] == 1


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
async def test_talabat_enrich_session_pulls_global_entity_from_account(monkeypatch):
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={"global_entity_id": "TB_KW"})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    session = ss.LoadedSession(channel="talabat", account_ref="", tokens={})
    out = await ss.enrich_session(SimpleNamespace(), session)
    assert out.tokens["global_entity_id"] == "TB_KW"


async def test_talabat_enrich_session_is_a_noop_without_extras(monkeypatch):
    """No extras → the same session object → byte-identical requests as before."""
    from app.services.aggregators import session_store as ss

    async def fake_load(_db, _channel, _ref):
        return SimpleNamespace(extras={})

    monkeypatch.setattr("app.services.aggregators.account_store.load", fake_load)
    session = ss.LoadedSession(channel="talabat", account_ref="", tokens={})
    out = await ss.enrich_session(SimpleNamespace(), session)
    assert out is session
    assert "global_entity_id" not in out.tokens


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
    # Talabat, Noon and Careem go out behind TLS impersonation (all Cloudflare/
    # Akamai/PerimeterX fronted); Deliveroo does not.
    assert ingest.PROVIDERS["talabat"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["noon"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["careem"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["deliveroo"].uses_tls_impersonation is False


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
