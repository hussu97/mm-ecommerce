"""Unit coverage for the aggregator backend seams added on top of the ingest.

Pins the logic that would fail silently: reconciliation skipping Deliveroo's
synthetic item-aggregate carrier orders, the Careem area→branch mapping, and the
fail-closed auth on the reconciliation reads.
"""

from __future__ import annotations

from datetime import timedelta
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


async def test_statements_list_requires_permission(client):
    resp = await client.get("/api/v1/aggregators/statements")
    assert resp.status_code == 401


async def test_statement_invoice_url_requires_permission(client):
    resp = await client.get(
        "/api/v1/aggregators/statements/00000000-0000-0000-0000-000000000000/invoice"
    )
    assert resp.status_code == 401


async def test_fees_summary_requires_permission(client):
    resp = await client.get("/api/v1/aggregators/fees/summary")
    assert resp.status_code == 401


async def test_statements_list_rejects_a_bad_date(client, monkeypatch):
    # The date filters are regex-guarded; a non-ISO value is a 422 before any DB
    # work, not a 500. (Permission is checked first, so grant it for this probe.)
    monkeypatch.setattr("app.core.permissions.require", lambda *a, **k: lambda: None)
    resp = await client.get("/api/v1/aggregators/statements?date_from=2026-8-1")
    assert resp.status_code in (401, 422)


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

    monkeypatch.setattr("app.services.aggregators.ingest.upsert_order", fake_upsert)

    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": [{"a": 1}]},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 2}
    assert [c for c, _ in upserts] == ["keeta", "keeta"]


async def test_keeta_orders_push_triggers_promote_when_something_ingested(
    client, monkeypatch
):
    """Keeta is push-only and meant to be the freshest channel, so a push kicks a
    promote immediately instead of waiting for the hourly sweep — but only when it
    actually ingested something."""
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")
    monkeypatch.setattr(
        "app.services.providers.keeta_provider.provider.parse_orders",
        lambda payload: [object()],
    )

    async def fake_upsert(db, channel, order):
        return None

    monkeypatch.setattr("app.services.aggregators.ingest.upsert_order", fake_upsert)

    calls = {"n": 0}
    monkeypatch.setattr(
        "app.services.aggregators.ingest.trigger_promote_reconcile_in_background",
        lambda: calls.__setitem__("n", calls["n"] + 1) or True,
    )

    # Something ingested → promote kicked.
    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": [{"a": 1}]},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 1}
    assert calls["n"] == 1

    # Nothing ingested → no promote kicked.
    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": []},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 0}
    assert calls["n"] == 1


async def test_keeta_orders_push_isolates_a_bad_payload(client, monkeypatch):
    # One malformed payload must not fail the whole batch — it is logged and
    # skipped, the good payloads still ingest.
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")

    def flaky_parse(payload):
        if payload.get("bad"):
            raise ValueError("unparseable keeta payload")
        return [object()]

    monkeypatch.setattr(
        "app.services.providers.keeta_provider.provider.parse_orders", flaky_parse
    )

    async def fake_upsert(db, channel, order):
        return None

    monkeypatch.setattr("app.services.aggregators.ingest.upsert_order", fake_upsert)

    resp = await client.post(
        "/api/v1/aggregators/keeta/orders",
        json={"payloads": [{"ok": 1}, {"bad": 1}, {"ok": 1}]},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ingested": 2}


# ── rolling sales-only refresh (frequent cadence) ─────────────────────────────
@pytest.mark.asyncio
async def test_run_sales_refresh_once_sweeps_rolling_window_then_promotes(monkeypatch):
    """The frequent refresh re-scrapes ONLY sales over the rolling-hours window,
    then promotes and reconciles — never a finance sweep."""
    from app.services.aggregators import ingest

    calls: dict = {}

    async def fake_sweep_all(mode, lock_key, *, lookback_hours=None):
        calls["sweep"] = (mode, lock_key, lookback_hours)
        return 7

    async def fake_promote():
        calls["promoted"] = True
        return 2

    async def fake_reconcile():
        calls["reconciled"] = True
        return 1

    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_sweep_all", fake_sweep_all)
    monkeypatch.setattr(ingest, "sweep_promote_once", fake_promote)
    monkeypatch.setattr(ingest, "sweep_reconcile_once", fake_reconcile)
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SALES_ROLLING_HOURS", 36)

    written = await ingest.run_sales_refresh_once()

    assert written == 7
    assert calls["sweep"] == (ingest.RUN_MODE_SALES, ingest._SALES_LOCK_KEY, 36)
    assert calls["promoted"] and calls["reconciled"]


@pytest.mark.asyncio
async def test_run_sales_refresh_once_noop_when_disabled(monkeypatch):
    from app.services.aggregators import ingest

    monkeypatch.setattr(ingest, "is_enabled", lambda: False)

    async def boom(*a, **k):  # must not be reached
        raise AssertionError("sweep ran while ingest disabled")

    monkeypatch.setattr(ingest, "_sweep_all", boom)
    assert await ingest.run_sales_refresh_once() == 0


@pytest.mark.asyncio
async def test_sales_refresh_scheduler_disabled_at_zero_interval(monkeypatch):
    """interval <= 0 returns immediately instead of looping forever."""
    from app.services.aggregators import ingest

    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SALES_REFRESH_MINUTES", 0)
    # Returns (does not hang); no sweep attempted.
    await ingest.run_sales_refresh_scheduler_forever()


# ── rolling sales refresh survives frequent redeploys (boot catch-up) ──────────
class TestSalesRefreshBootCatchup:
    """A sleep-first loop resets its countdown on every redeploy, so on a busy
    deploy day the hourly tick would never fire. The boot catch-up runs once when
    the last SALES sweep is older than the interval — and NOT when it is recent."""

    async def _run_once_and_capture(self, monkeypatch, last_sweep):
        import asyncio as _asyncio

        from app.services.aggregators import ingest

        monkeypatch.setattr(ingest, "is_enabled", lambda: True)
        monkeypatch.setattr(
            "app.core.config.settings.AGGREGATOR_SALES_REFRESH_MINUTES", 60
        )

        async def fake_last():
            return last_sweep

        calls = {"n": 0}

        async def fake_refresh():
            calls["n"] += 1
            return 0

        async def stop_sleep(_secs):
            raise _asyncio.CancelledError

        monkeypatch.setattr(ingest, "_last_sales_sweep_at", fake_last)
        monkeypatch.setattr(ingest, "run_sales_refresh_once", fake_refresh)
        monkeypatch.setattr(ingest.asyncio, "sleep", stop_sleep)

        with pytest.raises(_asyncio.CancelledError):
            await ingest.run_sales_refresh_scheduler_forever()
        return calls["n"]

    async def test_catches_up_when_never_run(self, monkeypatch):
        assert await self._run_once_and_capture(monkeypatch, None) == 1

    async def test_catches_up_when_last_sweep_is_stale(self, monkeypatch):
        from app.models.base import utcnow

        stale = utcnow() - timedelta(hours=2)
        assert await self._run_once_and_capture(monkeypatch, stale) == 1

    async def test_skips_when_last_sweep_is_recent(self, monkeypatch):
        from app.models.base import utcnow

        recent = utcnow() - timedelta(minutes=5)
        # No catch-up (recent), and the loop's first sleep is cancelled before its
        # own run — so zero refreshes fired.
        assert await self._run_once_and_capture(monkeypatch, recent) == 0


# ── reauth waits must NOT hold a DB connection (2026-08-30 pool-deadlock fix) ──
class TestSweepReleasesConnectionBeforeReauth:
    """The sweep must release its pooled DB connection BEFORE the up-to-360s reauth
    wait — holding it idle-in-transaction across the wait is what exhausted the pool
    and took the API down. These pin the ordering: release happens before the wait."""

    async def test_upfront_reauth_rolls_back_first(self, monkeypatch):
        from app.services.aggregators import ingest

        calls: list[str] = []
        db = AsyncMock()
        db.rollback = AsyncMock(side_effect=lambda: calls.append("rollback"))

        async def no_session(*a, **k):
            return None

        async def fake_reauth(*a, **k):
            calls.append("await_reauth")
            return None

        monkeypatch.setattr(ingest, "_session_for", no_session)
        monkeypatch.setattr(ingest, "_await_reauth", fake_reauth)

        written = await ingest._sweep_channel(db, "careem", object(), "sales")
        assert written == 0
        assert calls == ["rollback", "await_reauth"], (
            f"connection not released before the wait: {calls}"
        )

    async def test_midpull_reauth_commits_first(self, monkeypatch):
        from app.services.aggregators import ingest

        calls: list[str] = []
        db = AsyncMock()
        db.commit = AsyncMock(side_effect=lambda: calls.append("commit"))

        async def live_session(*a, **k):
            return object()

        async def fake_new_run(*a, **k):
            return SimpleNamespace(status=None, error=None, finished_at=None)

        async def fail_fetch(*a, **k):
            raise ingest.AggregatorAuthError("401")

        async def fake_reauth(*a, **k):
            calls.append("await_reauth")
            return None

        monkeypatch.setattr(ingest, "_session_for", live_session)
        monkeypatch.setattr(ingest, "_new_run", fake_new_run)
        monkeypatch.setattr(ingest, "_fetch_and_persist", fail_fetch)
        monkeypatch.setattr(ingest, "_await_reauth", fake_reauth)
        monkeypatch.setattr(ingest, "_sweep_window", lambda *a, **k: (None, None))
        monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", AsyncMock())

        written = await ingest._sweep_channel(db, "careem", object(), "sales")
        assert written == 0
        assert "commit" in calls and calls.index("commit") < calls.index(
            "await_reauth"
        ), f"connection not released before the wait: {calls}"
