"""Unit coverage for the aggregator backend seams added on top of the ingest.

Pins the logic that would fail silently: reconciliation skipping Deliveroo's
synthetic item-aggregate carrier orders, the Careem area→branch mapping, and the
fail-closed auth on the reconciliation reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.aggregators import ingest, mapping, reconcile
from app.services.aggregators.session_store import LoadedSession
from app.services.providers.careem_provider import CareemClient


class _FakeSessionCtx:
    """A minimal `async with AsyncSessionFactory() as db` stand-in for _await_reauth
    tests — session_store calls are mocked, so the db only needs to commit."""

    async def __aenter__(self):
        return SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    async def __aexit__(self, *_a):
        return False


def _fake_session_factory():
    return _FakeSessionCtx()


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

    async def fake_finalize(mode, since, until, *, not_before):
        calls["finalized"] = mode

    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "_sweep_all", fake_sweep_all)
    monkeypatch.setattr(ingest, "sweep_promote_once", fake_promote)
    monkeypatch.setattr(ingest, "sweep_reconcile_once", fake_reconcile)
    monkeypatch.setattr(ingest, "_finalize_run_coverage", fake_finalize)
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SALES_ROLLING_HOURS", 36)

    written = await ingest.run_sales_refresh_once()

    assert written == 7
    assert calls["sweep"] == (ingest.RUN_MODE_SALES, ingest._SALES_LOCK_KEY, 36)
    assert calls["promoted"] and calls["reconciled"]
    # After promote, the sweep run rows get their promotion split filled in.
    assert calls["finalized"] == ingest.RUN_MODE_SALES


def test_retrieved_from_detail_is_mode_shaped():
    """A sweep's immediate 'retrieved' figures, from _fetch_and_persist's detail:
    orders for a sales run, statements/payouts for a finance run — so the Runs table
    shows what a scheduled run pulled before the global promote fills in promotion."""
    from app.services.aggregators import ingest

    assert ingest._retrieved_from_detail(ingest.RUN_MODE_SALES, {"orders": 14}) == {
        "orders_retrieved": 14
    }
    assert ingest._retrieved_from_detail(
        ingest.RUN_MODE_FINANCE, {"statements": 3, "payouts": 2}
    ) == {"statements_total": 3, "payouts_total": 2}
    # Missing keys default to 0 — never a KeyError on a sparse detail.
    assert ingest._retrieved_from_detail(ingest.RUN_MODE_SALES, {}) == {
        "orders_retrieved": 0
    }


def test_window_business_dates_are_dubai_local():
    """A sweep window's UTC datetimes map to Dubai (+4) business dates, so the run
    coverage is scoped the way the backfill scopes its explicit range."""
    from datetime import datetime, timezone

    from app.services.aggregators import ingest

    since = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)  # 00:00 Dubai, 31st
    until = datetime(2026, 8, 31, 18, 59, tzinfo=timezone.utc)  # 22:59 Dubai, 31st
    lo, hi = ingest._window_business_dates(since, until)
    assert lo.isoformat() == "2026-08-31"
    assert hi.isoformat() == "2026-08-31"


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
class _DummyCtx:
    """A stand-in `async with AsyncSessionFactory()` whose session is an AsyncMock —
    for the sweep's short-lived session-load/finalise blocks when the DB-touching
    steps around them are themselves patched out."""

    async def __aenter__(self):
        return AsyncMock()

    async def __aexit__(self, *exc):
        return False


def _dummy_factory():
    return _DummyCtx()


class TestSweepHoldsNoConnectionAcrossReauth:
    """F-AGG-7: the sweep opens the run on its OWN committed session, awaits the
    marketplace fetch with NO session held, and finalises on a fresh session — so no
    pooled connection is ever held across the up-to-360s reauth wait (what exhausted
    the pool and took the API down). `_sweep_channel` no longer takes a `db`; these
    pin that the fetch is sessionless and the reauth path never opens/holds one."""

    async def test_dead_session_upfront_reauths_without_opening_a_run(
        self, monkeypatch
    ):
        from app.services.aggregators import ingest

        calls: list[str] = []

        async def no_session(*a, **k):
            return None

        async def fake_reauth(*a, **k):
            calls.append("await_reauth")
            return None

        async def fake_open_run(*a, **k):
            calls.append("open_run")
            return 1

        monkeypatch.setattr(ingest, "_session_for", no_session)
        monkeypatch.setattr(ingest, "_await_reauth", fake_reauth)
        monkeypatch.setattr(ingest, "_open_run", fake_open_run)
        monkeypatch.setattr(ingest, "AsyncSessionFactory", _dummy_factory)

        written = await ingest._sweep_channel("careem", object(), "sales")
        assert written == 0
        assert calls == ["await_reauth"]  # reauth tried, and NO run row opened

    async def test_midpull_auth_death_reauths_then_fails_the_run(self, monkeypatch):
        from app.services.aggregators import ingest

        calls: list = []

        async def live_session(*a, **k):
            return object()

        async def fake_open_run(*a, **k):
            return 7

        async def fail_fetch(*a, **k):
            # The fetch is what raises — and it is called with NO db session in scope.
            raise ingest.AggregatorAuthError("401")

        async def fake_reauth(*a, **k):
            calls.append("await_reauth")
            return None

        async def fake_finalize(run_id, *, status, **k):
            calls.append(("finalize", run_id, status))

        monkeypatch.setattr(ingest, "_session_for", live_session)
        monkeypatch.setattr(ingest, "_open_run", fake_open_run)
        monkeypatch.setattr(ingest, "_fetch_channel_mode", fail_fetch)
        monkeypatch.setattr(ingest, "_await_reauth", fake_reauth)
        monkeypatch.setattr(ingest, "_finalize_run_status", fake_finalize)
        monkeypatch.setattr(ingest, "_sweep_window", lambda *a, **k: (None, None))
        monkeypatch.setattr(ingest, "AsyncSessionFactory", _dummy_factory)

        written = await ingest._sweep_channel("careem", object(), "sales")
        assert written == 0
        # Reauth was tried once, then the run was finalised FAILED on a fresh session.
        assert "await_reauth" in calls
        assert ("finalize", 7, ingest.RUN_FAILED) in calls


# ── the reauth poll must keep what the provider's prepare wrote ───────────────
async def test_reauth_poll_commits_what_prepare_session_wrote(monkeypatch):
    """`_session_for` runs the provider's `prepare_session`, and for a password
    channel (Deliveroo) that can perform a real login and write the minted token.
    Leaving the poll block without committing threw that write away, so the next
    poll logged in again — four Deliveroo logins in eight seconds in the 2026-09-06
    outage logs."""
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_REAUTH_WAIT_SECONDS", 360)
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_REAUTH_POLL_SECONDS", 0.01)
    commits: list[str] = []

    class _Ctx:
        async def __aenter__(self):
            return SimpleNamespace(
                commit=AsyncMock(side_effect=lambda: commits.append("commit")),
                rollback=AsyncMock(),
            )

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(ingest, "AsyncSessionFactory", lambda: _Ctx())
    monkeypatch.setattr(
        ingest.session_store,
        "load",
        AsyncMock(
            return_value=LoadedSession(
                channel="deliveroo",
                account_ref="",
                status="needs_bootstrap",
                reauth_backoff_until=None,
            )
        ),
    )
    monkeypatch.setattr(
        ingest.session_store, "session_unusable_reason", lambda s: "needs_bootstrap"
    )
    monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", AsyncMock())

    async def fake_session_for(db, ch, prov):
        return object()

    monkeypatch.setattr(ingest, "_session_for", fake_session_for)

    out = await ingest._await_reauth("deliveroo", object())
    assert out is not None
    # One commit for the needs_bootstrap flag, one for the poll that ran prepare.
    assert len(commits) >= 2


# ── gap 2: _await_reauth honours the worker's published heal backoff ───────────
class TestAwaitReauthBackoff:
    """When the worker publishes a heal backoff (via reauth_backoff_until), the
    ingest must not burn its full reauth wait on a login the worker won't re-drive
    in time — it flags the session and bails, letting a later tick recover it."""

    async def test_skips_wait_when_backed_off_past_the_wait(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.config.settings.AGGREGATOR_REAUTH_WAIT_SECONDS", 360
        )
        monkeypatch.setattr(ingest, "AsyncSessionFactory", _fake_session_factory)
        loaded = LoadedSession(
            channel="careem",
            account_ref="",
            status="needs_bootstrap",
            reauth_backoff_until=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        monkeypatch.setattr(
            ingest.session_store, "load", AsyncMock(return_value=loaded)
        )
        monkeypatch.setattr(
            ingest.session_store,
            "session_unusable_reason",
            lambda s: "needs_bootstrap",
        )
        monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", AsyncMock())
        polled = {"n": 0}

        async def fake_session_for(db, ch, prov):
            polled["n"] += 1
            return object()

        monkeypatch.setattr(ingest, "_session_for", fake_session_for)

        out = await ingest._await_reauth("careem", object())
        assert out is None
        assert polled["n"] == 0  # never waited/polled — worker won't heal in time

    async def test_waits_when_no_backoff_and_returns_recovered_session(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "app.core.config.settings.AGGREGATOR_REAUTH_WAIT_SECONDS", 360
        )
        monkeypatch.setattr(
            "app.core.config.settings.AGGREGATOR_REAUTH_POLL_SECONDS", 0.01
        )
        monkeypatch.setattr(ingest, "AsyncSessionFactory", _fake_session_factory)
        loaded = LoadedSession(
            channel="careem",
            account_ref="",
            status="needs_bootstrap",
            reauth_backoff_until=None,  # nothing published → wait as before
        )
        monkeypatch.setattr(
            ingest.session_store, "load", AsyncMock(return_value=loaded)
        )
        monkeypatch.setattr(
            ingest.session_store, "session_unusable_reason", lambda s: "cookie expired"
        )
        monkeypatch.setattr(ingest.session_store, "mark_needs_bootstrap", AsyncMock())
        recovered = object()

        async def fake_session_for(db, ch, prov):
            return recovered  # daemon brought it back on the first poll

        monkeypatch.setattr(ingest, "_session_for", fake_session_for)

        out = await ingest._await_reauth("careem", object())
        assert out is recovered


async def test_reauth_backoff_endpoint_sets_value(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "tok")
    captured = {}

    async def fake_set(db, channel, *, backoff_until, account_ref=""):
        captured["channel"] = channel
        captured["until"] = backoff_until

    monkeypatch.setattr(
        "app.services.aggregators.session_store.set_reauth_backoff", fake_set
    )
    resp = await client.post(
        "/api/v1/aggregators/worker/reauth-backoff",
        json={"channel": "careem", "backoff_until": "2026-08-30T13:00:00+00:00"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 204
    assert captured["channel"] == "careem"
    assert captured["until"] is not None


async def test_reauth_backoff_endpoint_is_fail_closed(client, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.AGGREGATOR_SESSION_PUSH_TOKEN", "the-real-token"
    )
    resp = await client.post(
        "/api/v1/aggregators/worker/reauth-backoff",
        json={"channel": "careem", "backoff_until": None},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401
