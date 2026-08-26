"""Unit coverage for the aggregator backend seams added on top of the ingest.

Pins the logic that would fail silently: reconciliation skipping Deliveroo's
synthetic item-aggregate carrier orders, the Careem area→branch mapping, and the
fail-closed auth on the reconciliation reads.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.aggregators import mapping, reconcile
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.careem_provider import CareemClient


# ── reconcile skips Deliveroo synthetic carrier orders ────────────────────────
def test_is_carrier_order_by_prefix_and_status():
    assert reconcile._is_carrier_order(
        SimpleNamespace(external_order_id="deliveroo-items:2026-08-26", status="x")
    )
    assert reconcile._is_carrier_order(
        SimpleNamespace(external_order_id="12345", status="items_aggregate")
    )
    assert not reconcile._is_carrier_order(
        SimpleNamespace(external_order_id="12345", status="delivered")
    )


async def test_reconcile_order_skips_a_carrier_order_without_writing():
    db = AsyncMock()
    agg = SimpleNamespace(
        external_order_id="deliveroo-items:2026-08-26",
        status="items_aggregate",
        branch_id=uuid4(),
        channel="deliveroo",
    )
    await reconcile.reconcile_order(db, agg)
    # Returned before touching the DB — no reconciliation row written.
    db.execute.assert_not_called()
    db.scalar.assert_not_called()


# ── Careem area → branch mapping ──────────────────────────────────────────────
_OUTLETS = [
    {
        "external_outlet_id": "1067984",
        "external_brand_id": "1029671",
        "external_company_id": "1026653",
        "area_name": "Barsha Heights",
        "active": True,
    },
    {
        "external_outlet_id": "1087801",
        "external_brand_id": "1029671",
        "external_company_id": "1026653",
        "area_name": "Al Majaz",  # Sharjah, shut → active False
        "active": False,
    },
    {
        "external_outlet_id": "9999999",
        "external_brand_id": "1029671",
        "external_company_id": "1026653",
        "area_name": "Nowhere-Ville",  # unknown area → skipped
        "active": True,
    },
]


@pytest.fixture
def fake_branches():
    """A branch id for each hint the default area map resolves to."""
    return {
        "barsha": uuid4(),
        "sharjah": uuid4(),
    }


async def test_map_careem_maps_known_areas_and_skips_unknown(
    monkeypatch, fake_branches
):
    async def fake_discover(session):
        return list(_OUTLETS)

    async def fake_resolve(db, hint):
        return fake_branches.get(hint)

    calls: list[dict] = []

    async def fake_upsert(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.providers.careem_provider.provider.discover_outlets",
        fake_discover,
    )
    monkeypatch.setattr(mapping, "_resolve_branch", fake_resolve)
    monkeypatch.setattr(mapping, "upsert_branch_map", fake_upsert)

    db = AsyncMock()
    mapped = await mapping.map_careem(
        db, LoadedSession(channel="careem", account_ref="")
    )

    # Barsha and Al Majaz map; Nowhere-Ville has no area entry and is skipped.
    assert mapped == 2
    by_outlet = {c["external_outlet_id"]: c for c in calls}
    assert set(by_outlet) == {"1067984", "1087801"}
    assert by_outlet["1067984"]["branch_id"] == fake_branches["barsha"]
    assert by_outlet["1067984"]["is_active"] is True
    # Al Majaz resolves to Sharjah and carries Careem's shut flag through.
    assert by_outlet["1087801"]["branch_id"] == fake_branches["sharjah"]
    assert by_outlet["1087801"]["is_active"] is False
    assert by_outlet["1067984"]["channel"] == "careem"


async def test_map_careem_skips_an_area_that_matches_no_branch(monkeypatch):
    async def fake_discover(session):
        return [
            {
                "external_outlet_id": "1067984",
                "external_brand_id": "b",
                "external_company_id": "c",
                "area_name": "Barsha Heights",
                "active": True,
            }
        ]

    async def fake_resolve(db, hint):
        return None  # hint matched no branch

    calls: list[dict] = []

    async def fake_upsert(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.providers.careem_provider.provider.discover_outlets",
        fake_discover,
    )
    monkeypatch.setattr(mapping, "_resolve_branch", fake_resolve)
    monkeypatch.setattr(mapping, "upsert_branch_map", fake_upsert)

    db = AsyncMock()
    mapped = await mapping.map_careem(
        db, LoadedSession(channel="careem", account_ref="")
    )
    assert mapped == 0
    assert calls == []


def test_upsert_branch_map_is_a_real_helper():
    # Guards the public surface the seed/mapping callers import.
    assert callable(mapping.upsert_branch_map)
    assert callable(mapping.ensure_foodics_map)
    assert isinstance(CareemClient().channel, str)


# ── reconciliation reads are permission-gated (fail-closed) ───────────────────
async def test_reconciliation_list_requires_permission(client):
    resp = await client.get("/api/v1/aggregators/reconciliation")
    assert resp.status_code == 401


async def test_reconciliation_summary_requires_permission(client):
    resp = await client.get("/api/v1/aggregators/reconciliation/summary")
    assert resp.status_code == 401


async def test_keeta_orders_push_is_fail_closed(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": []},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


async def test_keeta_orders_push_ingests_parsed_orders(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")

    parsed = [object(), object()]
    monkeypatch.setattr(
        "app.services.providers.keeta_provider.provider.parse_orders",
        lambda payload: parsed,
    )

    upserts: list = []

    async def fake_upsert(db, channel, order):
        upserts.append((channel, order))

    monkeypatch.setattr("app.api.v1.aggregators._upsert_order", fake_upsert)

    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": [{"a": 1}]},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 2}
    assert [c for c, _ in upserts] == ["keeta", "keeta"]
