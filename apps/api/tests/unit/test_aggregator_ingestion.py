"""Unit coverage for the aggregator ingestion — the pure logic, no DB.

The DB paths run against Postgres in production; what is worth pinning here is
the logic that would go wrong silently: the session envelope, the Careem scope
flattening (asserted against the real console payload), the reconciliation
deltas, the request-fingerprint assembly, and the fail-closed auth on the one
write path.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.services.aggregators import crypto
from app.services.aggregators.normalized import GRAIN_LINE
from app.services.aggregators.reconcile import _item_discrepancy
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.careem_provider import CareemClient


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


# ── provider registry ─────────────────────────────────────────────────────────
def test_registry_has_the_four_httpx_channels_and_not_keeta():
    from app.services.aggregators import ingest

    ingest.PROVIDERS.clear()
    ingest._register_providers()
    assert set(ingest.PROVIDERS) == {"careem", "deliveroo", "talabat", "noon"}
    # Talabat and Noon go out behind TLS impersonation; Careem/Deliveroo do not.
    assert ingest.PROVIDERS["talabat"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["noon"].uses_tls_impersonation is True
    assert ingest.PROVIDERS["careem"].uses_tls_impersonation is False
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
