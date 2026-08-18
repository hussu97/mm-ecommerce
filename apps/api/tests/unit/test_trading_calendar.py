"""
Closed days, at the level of `core.trading_hours` itself.

`test_delivery_estimate.py` tests what a *promise* does about a holiday. This
tests the layer underneath it — the one the dispatcher's `kitchen_is_open` is
also an alias of — because the argument for putting closures here rather than
in the resolver was that one definition cannot disagree with itself. That is
only true if this layer is right on its own.

The case that matters most is a branch trading past midnight. A holiday there
has to shut the shift, not the calendar box: 01:00 on the 9th is the 8th's
night, and a closure on the 9th that shuts it would be wrong by a day in the
direction nobody checks.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import trading_hours

DUBAI = ZoneInfo("Asia/Dubai")

#: The Sharjah kitchen's real day, and a late one that runs into tomorrow.
DAY = ("09:00", "23:00")
LATE = ("09:00", "02:00")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=DUBAI)


def on(day: int) -> date:
    return date(2026, 8, day)


def shut(*days: int) -> frozenset[str]:
    return frozenset(f"2026-08-{day:02d}" for day in days)


# ── which day an instant belongs to ──────────────────────────────────────────


@pytest.mark.parametrize(
    "moment,hours,expected",
    [
        # An ordinary day is the calendar date, whatever the hour.
        (at(8, 14), DAY, on(8)),
        (at(8, 23, 30), DAY, on(8)),
        (at(8, 7), DAY, on(8)),
        # A day running to 02:00 owns the small hours of the next date.
        (at(9, 1), LATE, on(8)),
        (at(9, 1, 59), LATE, on(8)),
        # 02:00 is the close, and half-open means it belongs to the new day.
        (at(9, 2), LATE, on(9)),
        (at(9, 14), LATE, on(9)),
    ],
)
def test_trading_date_follows_the_shift_not_the_calendar(moment, hours, expected):
    assert trading_hours.trading_date(moment, *hours) == expected


def test_trading_date_falls_back_to_the_calendar_when_hours_are_unreadable():
    """Matching the always-open reading: a bad record must not shift the day."""
    assert trading_hours.trading_date(at(9, 1), None, None) == on(9)
    assert trading_hours.trading_date(at(9, 1), "nonsense", "02:00") == on(9)


# ── is_open ──────────────────────────────────────────────────────────────────


def test_a_closed_day_is_shut_at_every_hour_of_it():
    for hour in (9, 14, 22):
        assert trading_hours.is_open(at(8, hour), *DAY) is True
        assert trading_hours.is_open(at(8, hour), *DAY, shut(8)) is False


def test_a_closure_shuts_the_shift_it_names_and_not_the_one_before_it():
    """
    The past-midnight case. 01:00 on the 9th is the 8th's night and survives a
    holiday on the 9th; 01:00 on the 10th is the 9th's night and does not.
    """
    assert trading_hours.is_open(at(9, 1), *LATE, shut(9)) is True
    assert trading_hours.is_open(at(10, 1), *LATE, shut(9)) is False


def test_a_closure_shuts_a_branch_whose_hours_cannot_be_read():
    """
    Unparseable hours are tolerated as always-open, deliberately. A date
    somebody typed into the admin is not a parse failure and gets no such
    benefit of the doubt.
    """
    assert trading_hours.is_open(at(8, 3), None, None) is True
    assert trading_hours.is_open(at(8, 3), None, None, shut(8)) is False


def test_an_unrelated_closure_changes_nothing():
    assert trading_hours.is_open(at(8, 14), *DAY, shut(20)) is True


# ── first_open_day ───────────────────────────────────────────────────────────


def test_first_open_day_is_the_day_itself_when_it_trades():
    assert trading_hours.first_open_day(on(8), shut(9, 10)) == on(8)
    assert trading_hours.first_open_day(on(8)) == on(8)


def test_first_open_day_steps_over_a_run_of_closures():
    assert trading_hours.first_open_day(on(8), shut(8, 9, 10)) == on(11)


def test_first_open_day_gives_up_rather_than_walking_forever():
    """
    Nothing stops an admin marking a year shut. A checkout that hangs looking
    for an opening is a worse failure than a promise made on a closed day, so
    the walk is bounded and returns what it was asked about.
    """
    everything = frozenset((on(1) + timedelta(days=n)).isoformat() for n in range(400))
    assert trading_hours.first_open_day(on(8), everything) == on(8)


# ── next_opening ─────────────────────────────────────────────────────────────


def test_next_opening_skips_closed_days():
    assert trading_hours.next_opening(at(8, 23, 30), "09:00") == at(9, 9)
    assert trading_hours.next_opening(at(8, 23, 30), "09:00", shut(9, 10)) == at(11, 9)


def test_next_opening_on_a_closed_day_is_the_next_open_one():
    """Not today at nine — nobody is there today."""
    assert trading_hours.next_opening(at(8, 3), "09:00", shut(8)) == at(9, 9)


def test_next_opening_with_unreadable_hours_still_leaves_a_closed_day():
    """
    With no hours to read the answer is normally `moment` itself, which is the
    always-open reading. A closure is still a closure: it moves to midnight on
    the next day that trades rather than staying on the shut one.
    """
    assert trading_hours.next_opening(at(8, 14), None) == at(8, 14)
    assert trading_hours.next_opening(at(8, 14), None, shut(20)) == at(8, 14)
    assert trading_hours.next_opening(at(8, 14), None, shut(8)) == at(9, 0)


# ── is_after_close is deliberately holiday-blind ─────────────────────────────


def test_is_after_close_ignores_closures_on_purpose():
    """
    It answers "has today's trading finished", and on a holiday there was none
    to finish. Rule 3 of the promise walks its base day forward afterwards,
    which covers the closure whatever this says — and teaching this the same
    walk would make two differently-shaped versions of it.
    """
    assert trading_hours.is_after_close(at(8, 23, 30), *DAY) is True
    assert trading_hours.is_after_close(at(8, 14), *DAY) is False
