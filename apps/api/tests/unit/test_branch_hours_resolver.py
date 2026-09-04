"""
The weekly-schedule resolver, at the level of `branch_hours_service` itself.

The DB-backed `schedule()` and the folding into `closed_dates_for` are covered by
integration tests (they need real rows). This tests the pure pieces every one of
those builds on: the Sunday=0 weekday mapping, reading a day's window out of a
schedule, and walking forward to the next open day. If the weekday arithmetic is
off by one here, a branch closes on the wrong day everywhere at once.
"""

from __future__ import annotations

from datetime import date

from app.services import branch_hours_service as bh

# 2026-09-06 is a Sunday; the week runs Sun..Sat = weekday 0..6.
SUN = date(2026, 9, 6)
MON = date(2026, 9, 7)
SAT = date(2026, 9, 12)


def test_model_weekday_sunday_is_zero() -> None:
    assert bh.model_weekday(SUN) == 0
    assert bh.model_weekday(MON) == 1
    assert bh.model_weekday(SAT) == 6


def test_window_for_open_and_closed_days() -> None:
    # Open Sun–Fri 09:00–23:00, closed Saturday (weekday 6 absent).
    sched = {d: ("09:00", "23:00") for d in range(6)}
    assert bh.window_for(sched, SUN) == ("09:00", "23:00")
    assert bh.window_for(sched, SAT) is None  # closed weekday


def test_window_for_no_schedule_is_none() -> None:
    assert bh.window_for(None, SUN) is None


def test_next_open_window_skips_closed_day() -> None:
    # Closed Saturday; from a Saturday the next open window is Sunday's.
    sched = {d: ("08:00", "22:00") for d in range(6)}
    assert bh.next_open_window(sched, SAT) == ("08:00", "22:00")
    # Per-day hours differ: Sunday later than the rest.
    sched[0] = ("10:00", "20:00")
    assert bh.next_open_window(sched, SAT) == ("10:00", "20:00")


def test_next_open_window_none_when_never_open() -> None:
    assert bh.next_open_window({}, SUN) is None
    assert bh.next_open_window(None, SUN) is None
