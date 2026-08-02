"""
Which run an order joins, and when that run leaves.

All of this is clock arithmetic in Dubai time, and clock arithmetic is where
delivery scheduling goes wrong: an order landing exactly on a boundary, a slot
that runs past midnight, a window whose end has already been and gone. Each of
those is a real order sitting in a box while nobody comes for it, so each has a
test.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.delivery_batch import DeliveryBatchWindow
from app.services.batching_service import find_window, next_dispatch_at, overlapping

DUBAI = ZoneInfo("Asia/Dubai")


def window(
    label: str, start: str, end: str, active: bool = True
) -> DeliveryBatchWindow:
    sh, sm = (int(p) for p in start.split(":"))
    eh, em = (int(p) for p in end.split(":"))
    return DeliveryBatchWindow(
        label=label,
        start_hour=sh,
        start_minute=sm,
        end_hour=eh,
        end_minute=em,
        is_active=active,
    )


#: The shop's own schedule: long in the morning when orders trickle, an hour at
#: a time through the evening when 58% of them arrive.
SEEDED = [
    window("Batch 1", "00:00", "12:00"),
    window("Batch 2", "12:00", "18:00"),
    window("Batch 3", "18:00", "21:00"),
    window("Batch 4", "21:00", "22:00"),
    window("Batch 5", "22:00", "23:00"),
    window("Batch 6", "23:00", "24:00"),
]


def dubai(hour: int, minute: int = 0, day: int = 2) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=DUBAI)


# ── which window ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hour,minute,expected,leaves",
    [
        (0, 0, "Batch 1", 12),
        (9, 30, "Batch 1", 12),
        (11, 59, "Batch 1", 12),
        # A boundary belongs to the window starting, not the one closing. An
        # order landing at 12:00 must not join a batch that is leaving now.
        (12, 0, "Batch 2", 18),
        (17, 59, "Batch 2", 18),
        (18, 0, "Batch 3", 21),
        (20, 59, "Batch 3", 21),
        (21, 0, "Batch 4", 22),
        (22, 0, "Batch 5", 23),
        (23, 0, "Batch 6", 24),
        (23, 59, "Batch 6", 24),
    ],
)
def test_the_seeded_schedule_covers_the_whole_day(hour, minute, expected, leaves):
    match = find_window(SEEDED, dubai(hour, minute))
    assert match is not None, f"{hour:02d}:{minute:02d} fell through the schedule"
    assert match.window.label == expected
    # Hour 24 is midnight closing the day — 00:00 tomorrow, not 23:59 tonight.
    assert match.dispatch_at == dubai(leaves % 24, day=2 + leaves // 24)


def test_utc_input_is_read_on_the_shop_clock():
    """
    Orders are stamped in UTC. 20:00 UTC is 00:00 in Dubai, which is the first
    batch of the *next* day — not the last of this one.
    """
    match = find_window(SEEDED, datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc))
    assert match is not None
    assert match.window.label == "Batch 1"
    assert match.dispatch_at == dubai(12, day=3)


def test_a_naive_timestamp_is_treated_as_utc():
    """Rather than as local, which would silently shift every batch by four hours."""
    match = find_window(SEEDED, datetime(2026, 8, 2, 20, 0))
    assert match is not None and match.window.label == "Batch 1"


def test_a_gap_in_the_schedule_matches_nothing():
    """
    Which is not a failure — an order with no window goes out on its own,
    immediately. A schedule with holes is a slower dispatch, never a stuck one.
    """
    sparse = [window("Evening", "18:00", "21:00")]
    assert find_window(sparse, dubai(9, 0)) is None
    assert find_window(sparse, dubai(21, 0)) is None
    assert find_window(sparse, dubai(19, 0)) is not None


def test_a_paused_window_matches_nothing():
    assert (
        find_window([window("Off", "00:00", "12:00", active=False)], dubai(9)) is None
    )


def test_no_windows_at_all_matches_nothing():
    assert find_window([], dubai(9)) is None


# ── past midnight ─────────────────────────────────────────────────────────────

LATE = [window("Late", "22:00", "02:00")]


def test_a_window_can_run_past_midnight():
    before = find_window(LATE, dubai(23, 0))
    assert before is not None
    # Started tonight, leaves tomorrow morning.
    assert before.dispatch_at == dubai(2, day=3)


def test_the_small_hours_belong_to_last_nights_window():
    after = find_window(LATE, dubai(1, 0, day=3))
    assert after is not None
    assert after.window.label == "Late"
    # Already inside the calendar day it closes on, so it leaves today at 02:00.
    assert after.dispatch_at == dubai(2, day=3)


def test_a_wrapping_window_still_has_an_outside():
    assert find_window(LATE, dubai(12, 0)) is None


# ── overlap ───────────────────────────────────────────────────────────────────


def test_a_clean_schedule_has_no_overlap():
    assert overlapping(SEEDED) is None


def test_two_windows_claiming_the_same_minute_are_caught():
    clash = overlapping([window("A", "18:00", "21:00"), window("B", "20:00", "22:00")])
    assert clash is not None
    assert {clash[0].label, clash[1].label} == {"A", "B"}


def test_touching_windows_do_not_count_as_overlapping():
    """18:00–21:00 and 21:00–22:00 share an instant, and the instant is the
    second one's — otherwise the seeded schedule would be rejected."""
    assert (
        overlapping([window("A", "18:00", "21:00"), window("B", "21:00", "22:00")])
        is None
    )


def test_a_wrapping_window_overlapping_a_morning_one_is_caught():
    """22:00–02:00 covers 01:00, and so does 00:00–06:00. Both halves of the
    wrap have to be checked, not just the evening one."""
    clash = overlapping(
        [window("Late", "22:00", "02:00"), window("Early", "00:00", "06:00")]
    )
    assert clash is not None


def test_a_paused_window_cannot_clash():
    """Pausing one is a legitimate way to resolve a clash without deleting it."""
    assert (
        overlapping(
            [window("A", "18:00", "21:00"), window("B", "20:00", "22:00", active=False)]
        )
        is None
    )


# ── next dispatch ─────────────────────────────────────────────────────────────


def test_next_dispatch_is_today_when_the_window_has_not_closed():
    assert next_dispatch_at(window("Batch 2", "12:00", "18:00"), dubai(13)) == dubai(18)


def test_next_dispatch_rolls_to_tomorrow_once_the_window_has_passed():
    assert next_dispatch_at(window("Batch 2", "12:00", "18:00"), dubai(19)) == dubai(
        18, day=3
    )
