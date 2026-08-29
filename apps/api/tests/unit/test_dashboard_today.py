"""
What the admin home dashboard is allowed to say about the trading day.

The home page used to read "today" off the last ten orders the browser had
loaded and sum the revenue client-side. Two things had to be true for the fix
to be worth shipping, and both are guarded here:

* "Today" is the shop's local day, resolved to an exact UTC instant — not
  `func.date(created_at)`, which in the Gulf books the first four hours after
  midnight to yesterday. `_day_bounds` is where that correctness lives.
* The figures are aggregated server-side and rounded once, through `money()`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.api.v1 import dashboard as mod

# Any two instants — the fake DB ignores the compiled statement, but SQLAlchemy
# still builds it, and `created_at >= None` is not a legal comparison.
_A = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
_B = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class _Result:
    """A stand-in for the object `AsyncSession.execute` returns."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _DB:
    """Answers each `execute` from a queue of prepared result rows."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        return self._results.pop(0)


# ── the day boundary, which is the whole point ────────────────────────────────


async def test_today_starts_at_local_midnight_not_utc(monkeypatch):
    """Dubai is UTC+4, so the shop day opens at 20:00 the previous day in UTC."""

    async def _tz(_db):
        return ZoneInfo("Asia/Dubai")

    monkeypatch.setattr(mod.business_day_service, "resolve_timezone", _tz)
    monkeypatch.setattr(
        mod.business_day_service, "shop_today", lambda tz=None: date(2026, 8, 25)
    )

    # No dates → the live single day. prior window is the same clock span yesterday.
    (
        from_date,
        to_date,
        tz_name,
        start,
        end,
        prior_start,
        prior_end,
    ) = await mod._range_bounds(_DB([]), None, None)

    assert from_date == date(2026, 8, 25)
    assert to_date is None
    assert tz_name == "Asia/Dubai"
    # 2026-08-25 00:00 Dubai == 2026-08-24 20:00 UTC.
    assert start == datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)
    assert end.tzinfo is timezone.utc
    # The growth baseline is exactly one day earlier, both ends.
    assert prior_start == start - timedelta(days=1)
    assert prior_end == end - timedelta(days=1)


async def test_range_bounds_span_and_prior_window(monkeypatch):
    """A [from, to] range spans full local days and grows against the equal window
    immediately before it."""

    async def _tz(_db):
        return ZoneInfo("Asia/Dubai")

    monkeypatch.setattr(mod.business_day_service, "resolve_timezone", _tz)

    (
        from_date,
        to_date,
        _tzn,
        start,
        end,
        prior_start,
        prior_end,
    ) = await mod._range_bounds(_DB([]), "2026-08-01", "2026-08-07")
    assert from_date == date(2026, 8, 1)
    assert to_date == "2026-08-07"
    # Opens at local midnight of the 1st (20:00 UTC on Jul 31)…
    assert start == datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    # …and closes one microsecond before local midnight after the 7th.
    assert end == datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc) - timedelta(
        microseconds=1
    )
    # 7-day span → the prior 7 days, both ends shifted back by 7.
    assert prior_start == start - timedelta(days=7)
    assert prior_end == end - timedelta(days=7)


async def test_range_bounds_rejects_a_half_range(monkeypatch):
    async def _tz(_db):
        return ZoneInfo("Asia/Dubai")

    monkeypatch.setattr(mod.business_day_service, "resolve_timezone", _tz)
    with pytest.raises(mod.BadRequestError):
        await mod._range_bounds(_DB([]), "2026-08-01", None)
    with pytest.raises(mod.BadRequestError):
        await mod._range_bounds(_DB([]), "2026-08-07", "2026-08-01")


# ── growth never divides by nothing ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "prior", "expected"),
    [
        (150.0, 100.0, 50.0),
        (50.0, 100.0, -50.0),
        (10.0, 0.0, 0.0),  # no rate off nothing
        (0.0, 0.0, 0.0),
        (100.0, 100.0, 0.0),
    ],
)
def test_growth_guards_the_off_nothing_case(current, prior, expected):
    assert mod._growth(current, prior) == expected


# ── breakdown maps labels and rounds money once ───────────────────────────────


async def test_breakdown_labels_and_rounds():
    rows = [
        (SimpleNamespace(value="online"), 5, "123.455"),
        ("cod", 2, "40.001"),
        (None, 1, "9.99"),
    ]
    labels = {"online": "Storefront", "cod": "Cash on delivery"}
    out = await mod._breakdown(
        _DB([_Result(rows)]), mod.Order.source, start=_A, end=_B, labels=labels
    )

    assert [(r.label, r.orders, r.revenue) for r in out] == [
        ("Storefront", 5, 123.46),  # ROUND_HALF_UP, quantised once
        ("Cash on delivery", 2, 40.0),
        ("Unknown", 1, 9.99),  # a null grouping key is named, not dropped
    ]


async def test_breakdown_titlecases_unlabelled_values():
    rows = [(SimpleNamespace(value="call_center"), 3, "0")]
    out = await mod._breakdown(_DB([_Result(rows)]), mod.Order.source, start=_A, end=_B)
    assert out[0].label == "Call Center"
