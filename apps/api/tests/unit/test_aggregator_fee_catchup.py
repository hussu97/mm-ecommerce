"""The nightly fee catch-up (`ingest.sweep_fee_gaps_once`).

A skipped night (Careem 16 Oct 2026) or a 403 from Talabat's finance API
(22-26 Oct 2026) used to leave a day fee-less for good, because the nightly
finance pass covers yesterday only. The catch-up re-runs the gap days.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import pytest

from app.services.aggregators import ingest


@asynccontextmanager
async def _no_db():
    yield None


@pytest.fixture
def harness(monkeypatch):
    calls: list[tuple] = []
    gaps: dict[str, list[date]] = {}

    async def fake_gaps(db, channel, today):
        return gaps.get(channel, [])

    async def fake_run_range(channels, a, b, *, modes, reconcile=True, promote=True):
        calls.append((channels[0], a, b, modes))
        return []

    monkeypatch.setattr(ingest, "is_enabled", lambda: True)
    monkeypatch.setattr(ingest, "AsyncSessionFactory", _no_db)
    monkeypatch.setattr(ingest, "fee_gap_dates", fake_gaps)
    monkeypatch.setattr(ingest, "run_range", fake_run_range)
    monkeypatch.setattr(
        ingest, "utcnow", lambda: datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
    )
    return calls, gaps


@pytest.mark.asyncio
async def test_no_gaps_runs_nothing(harness):
    calls, _ = harness
    assert await ingest.sweep_fee_gaps_once() == 0
    assert calls == []


@pytest.mark.asyncio
async def test_talabat_gap_days_are_reswept_one_by_one_through_sales(harness):
    calls, gaps = harness
    gaps["talabat"] = [date(2026, 10, 22), date(2026, 10, 23)]
    assert await ingest.sweep_fee_gaps_once() == 1
    assert calls == [
        ("talabat", date(2026, 10, 22), date(2026, 10, 22), (ingest.RUN_MODE_SALES,)),
        ("talabat", date(2026, 10, 23), date(2026, 10, 23), (ingest.RUN_MODE_SALES,)),
    ]


@pytest.mark.asyncio
async def test_talabat_catch_up_is_capped_per_pass(harness):
    calls, gaps = harness
    gaps["talabat"] = [date(2026, 10, d) for d in range(1, 20)]
    await ingest.sweep_fee_gaps_once()
    assert len(calls) == ingest._FEE_CATCHUP_MAX_TALABAT_DAYS


@pytest.mark.asyncio
async def test_statement_channel_reruns_finance_from_first_gap_to_yesterday(harness):
    calls, gaps = harness
    gaps["careem"] = [date(2026, 10, 16)]
    await ingest.sweep_fee_gaps_once()
    assert calls == [
        ("careem", date(2026, 10, 16), date(2026, 10, 29), (ingest.RUN_MODE_FINANCE,)),
    ]


@pytest.mark.asyncio
async def test_one_failing_channel_does_not_stop_the_rest(harness, monkeypatch):
    calls, gaps = harness
    gaps["talabat"] = [date(2026, 10, 22)]
    gaps["noon"] = [date(2026, 10, 10)]

    async def flaky(channels, a, b, *, modes, reconcile=True, promote=True):
        if channels[0] == "talabat":
            raise RuntimeError("403")
        calls.append((channels[0], a, b, modes))
        return []

    monkeypatch.setattr(ingest, "run_range", flaky)
    assert await ingest.sweep_fee_gaps_once() == 1
    assert [c[0] for c in calls] == ["noon"]


def test_every_httpx_channel_has_a_settle_lag():
    ingest._register_providers()
    assert set(ingest._FEE_SETTLE_DAYS) == set(ingest.PROVIDERS)
