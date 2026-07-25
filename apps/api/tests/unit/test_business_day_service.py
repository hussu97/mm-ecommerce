from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.branch import Branch
from app.services.business_day_service import business_date_for

DUBAI = ZoneInfo("Asia/Dubai")  # UTC+4, no DST


def _branch(cutoff: str = "04:00") -> Branch:
    branch = Branch()
    branch.business_day_start = cutoff
    return branch


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("utc_moment", "expected"),
    [
        # 12:00 Dubai on the 10th — squarely inside the trading day.
        (_utc(2026, 7, 10, 8), "2026-07-10"),
        # 23:30 Dubai on the 10th — still the 10th.
        (_utc(2026, 7, 10, 19, 30), "2026-07-10"),
        # 00:30 Dubai on the 11th — after midnight but before the 04:00 cut-off,
        # so it still belongs to the 10th's trading day.
        (_utc(2026, 7, 10, 20, 30), "2026-07-10"),
        # 03:59 Dubai on the 11th — the last minute of the 10th's trading day.
        (_utc(2026, 7, 10, 23, 59), "2026-07-10"),
        # 04:00 Dubai on the 11th — the new trading day begins.
        (_utc(2026, 7, 11, 0), "2026-07-11"),
    ],
)
def test_business_date_rolls_over_at_the_branch_cutoff(utc_moment, expected):
    assert business_date_for(_branch("04:00"), utc_moment, DUBAI) == expected


def test_midnight_cutoff_matches_the_calendar_date():
    branch = _branch("00:00")
    # 00:30 Dubai on the 11th with no cut-off is simply the 11th.
    assert business_date_for(branch, _utc(2026, 7, 10, 20, 30), DUBAI) == "2026-07-11"


def test_late_cutoff_extends_the_trading_day_further():
    branch = _branch("06:00")
    # 05:30 Dubai on the 11th still belongs to the 10th when the cut-off is 06:00.
    assert business_date_for(branch, _utc(2026, 7, 11, 1, 30), DUBAI) == "2026-07-10"


def test_malformed_cutoff_falls_back_to_four_am():
    branch = _branch("not-a-time")
    assert business_date_for(branch, _utc(2026, 7, 10, 23, 59), DUBAI) == "2026-07-10"
    assert business_date_for(branch, _utc(2026, 7, 11, 0, 1), DUBAI) == "2026-07-11"


def test_utc_input_is_converted_to_local_time_first():
    """
    22:00 UTC on the 9th is already 02:00 on the 10th in Dubai. With a midnight
    cut-off the trading date must follow local time (the 10th), not UTC (the 9th).
    """
    assert (
        business_date_for(_branch("00:00"), _utc(2026, 7, 9, 22), DUBAI) == "2026-07-10"
    )


def test_conversion_and_cutoff_compose():
    """
    Same instant, but a 04:00 cut-off pulls 02:00 local back into the previous
    trading day — so the answer is the 9th for a different reason than UTC.
    """
    assert (
        business_date_for(_branch("04:00"), _utc(2026, 7, 9, 22), DUBAI) == "2026-07-09"
    )
