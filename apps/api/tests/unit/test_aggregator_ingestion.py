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


def test_static_outlet_mapping_covers_all_channels_and_branches():
    from app.services.aggregators.mapping import OUTLET_KEY_TO_HINT, STATIC_OUTLETS

    # Keeta is mapped from an in-page capture (blank shop ids), not seeded here.
    assert set(STATIC_OUTLETS) == {"noon", "talabat", "deliveroo", "careem"}
    # Every seeded outlet has a real id and a resolvable branch hint.
    for outlets in STATIC_OUTLETS.values():
        for outlet_key, ids in outlets.items():
            assert ids.get("external_outlet_id"), outlet_key
            assert outlet_key in OUTLET_KEY_TO_HINT
    # Branch coverage genuinely differs per channel.
    assert set(STATIC_OUTLETS["noon"]) == {
        "sharjah",
        "barsha_heights",
        "dso",
        "karama",
    }
    assert set(STATIC_OUTLETS["talabat"]) == {"sharjah", "barsha_heights"}
    assert set(STATIC_OUTLETS["deliveroo"]) == {"sharjah", "barsha_heights", "dso"}
    # Careem's Sharjah outlet is permanently shut.
    assert STATIC_OUTLETS["careem"]["sharjah"]["is_active"] is False


async def test_keeta_is_bootstrap_driven_not_httpx():
    from app.services.providers.aggregator_base import AggregatorUnavailableError
    from app.services.providers.keeta_provider import provider as keeta

    session = LoadedSession(channel="keeta", account_ref="")
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    with pytest.raises(AggregatorUnavailableError):
        await keeta.fetch_sales(session, since=now, until=now)
