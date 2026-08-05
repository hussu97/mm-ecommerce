"""
Which run an order joins, and when that run leaves.

All of this is clock arithmetic in Dubai time, and clock arithmetic is where
delivery scheduling goes wrong: an order landing exactly on a boundary, a slot
that runs past midnight, a window whose end has already been and gone. Each of
those is a real order sitting in a box while nobody comes for it, so each has a
test.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.models.delivery_batch import (
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchWindow,
)
from app.models.order_delivery import OrderDelivery
from app.services import batching_service
from app.services.batching_service import (
    WindowMatch,
    _open_batch,
    find_window,
    next_dispatch_at,
    overlapping,
)

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


# ── noon Send never batches ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_noon_send_order_never_joins_a_run(monkeypatch):
    """
    Batching is a Lalamove product: one courier order carrying fifteen stops.
    noon Send's equivalent is a different endpoint with a cap of three that we
    do not use, so their orders always go out on their own — which is also what
    lets a noon Send zone promise an hour, since nothing waits for a window.

    Asserted rather than assumed, because the routing that guarantees it is one
    `if` in `assign_or_dispatch` and nothing else would notice it going.
    """
    dispatched: list[str] = []

    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="noon_send",
        zone_name="Sharjah Central",
        polygon_id=uuid.uuid4(),
        fee_charged=Decimal("15.00"),
    )
    order = SimpleNamespace(id=delivery.order_id, order_number="MM-1001")

    async def get_delivery(_db, _order_id):
        return delivery

    async def dispatch(_db, _order):
        dispatched.append("direct")
        return delivery

    async def windows(_db, _polygon_id):
        raise AssertionError("a noon Send zone must never be matched to a window")

    monkeypatch.setattr(batching_service.lalamove_service, "get_delivery", get_delivery)
    monkeypatch.setattr(batching_service.courier_service, "dispatch", dispatch)
    monkeypatch.setattr(batching_service, "active_windows", windows)

    result = await batching_service.assign_or_dispatch(object(), order)

    assert dispatched == ["direct"]
    assert result.batch_id is None


# ── one run, several zones ────────────────────────────────────────────────────
#
# Windows are per zone because density is local — the city fills a van in an
# hour, Fujairah never would. A van is not per zone. Two schedules closing on
# the same minute is two vans leaving the same kitchen for one journey's worth
# of work, and the base fare gets paid twice for it.


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """
    Answers `_open_batch`'s lookup from a list, in Python.

    The point of interest is *what the query is keyed on*, so the bound
    parameters are captured: a `polygon_id` appearing among them would mean runs
    had gone back to being per zone.
    """

    def __init__(self, existing: list[DeliveryBatch] | None = None):
        self.rows = list(existing or [])
        self.params: dict = {}

    async def execute(self, stmt):
        self.params = stmt.compile().params
        target = self.params.get("dispatch_at_1")
        status = self.params.get("status_1")
        return _Result(
            [b for b in self.rows if b.dispatch_at == target and b.status == status]
        )

    def add(self, obj):
        self.rows.append(obj)

    async def flush(self):
        return None


def _match(polygon_windows: DeliveryBatchWindow, leaves: datetime) -> WindowMatch:
    polygon_windows.id = uuid.uuid4()
    return WindowMatch(window=polygon_windows, dispatch_at=leaves)


def _pending(dispatch_at: datetime, polygon_id: uuid.UUID) -> DeliveryBatch:
    return DeliveryBatch(
        id=uuid.uuid4(),
        polygon_id=polygon_id,
        dispatch_at=dispatch_at,
        status=BatchStatusEnum.PENDING.value,
        window_label="Batch 5",
    )


async def test_two_zones_closing_together_share_one_run():
    city, outer = uuid.uuid4(), uuid.uuid4()
    midnight = dubai(0, 0, day=3)
    existing = _pending(midnight, city)
    db = _Session([existing])

    joined = await _open_batch(
        db, outer, _match(window("Batch 2", "17:00", "24:00"), midnight)
    )

    assert joined is existing, "the outer zone opened a second van for the same trip"


async def test_zones_closing_at_different_times_do_not_share():
    city, outer = uuid.uuid4(), uuid.uuid4()
    db = _Session([_pending(dubai(18, 0), city)])

    joined = await _open_batch(
        db, outer, _match(window("Batch 2", "17:00", "24:00"), dubai(17, 0))
    )

    assert joined.dispatch_at == dubai(17, 0)
    assert joined.polygon_id == outer


async def test_the_run_is_found_by_departure_time_not_by_zone():
    db = _Session()

    await _open_batch(
        db, uuid.uuid4(), _match(window("Batch 3", "18:00", "21:00"), dubai(21, 0))
    )

    assert "dispatch_at_1" in db.params
    assert not any("polygon" in key for key in db.params), (
        "the lookup is filtering by zone again, so a shared departure would "
        "open one run per zone"
    )


async def test_a_run_already_on_the_road_is_not_joined():
    """Half a batch leaving and half of it still being loaded is not a batch."""
    gone = _pending(dubai(21, 0), uuid.uuid4())
    gone.status = BatchStatusEnum.DISPATCHED.value
    db = _Session([gone])

    joined = await _open_batch(
        db, uuid.uuid4(), _match(window("Batch 3", "18:00", "21:00"), dubai(21, 0))
    )

    assert joined is not gone


# ── batching still batches ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_ordinary_order_joins_its_run(monkeypatch):
    """The saving batching exists for: an order waits for the van rather than
    booking one of its own."""
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="lalamove",
        zone_name="Dubai City",
        polygon_id=uuid.uuid4(),
        fee_charged=Decimal("25.00"),
    )
    order = SimpleNamespace(
        id=delivery.order_id,
        order_number="MM-20260805-003",
        user_id=None,
        email="someone@else.com",
    )
    leaves = dubai(12, 0)
    joined = SimpleNamespace(
        id=uuid.uuid4(), window_label="Batch 1", dispatch_at=leaves
    )

    async def get_delivery(_db, _order_id):
        return delivery

    async def never(_db, _order):
        raise AssertionError("an ordinary order should have joined a run")

    monkeypatch.setattr(batching_service.lalamove_service, "get_delivery", get_delivery)
    monkeypatch.setattr(batching_service.lalamove_service, "is_enabled", lambda: True)
    monkeypatch.setattr(batching_service.courier_service, "dispatch", never)
    monkeypatch.setattr(
        batching_service,
        "active_windows",
        AsyncMock(return_value=[window("Batch 1", "23:00", "12:00")]),
    )
    monkeypatch.setattr(
        batching_service,
        "find_window",
        lambda *_a: WindowMatch(
            window=window("Batch 1", "23:00", "12:00"), dispatch_at=leaves
        ),
    )
    monkeypatch.setattr(batching_service, "_open_batch", AsyncMock(return_value=joined))
    monkeypatch.setattr(
        batching_service, "_count_deliveries", AsyncMock(return_value=1)
    )

    class _Db:
        async def flush(self):
            return None

    result = await batching_service.assign_or_dispatch(_Db(), order)
    assert result.batch_id == joined.id
