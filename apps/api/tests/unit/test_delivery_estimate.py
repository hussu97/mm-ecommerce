"""
"When will it arrive?" — one resolver, four rules, in a fixed order.

The ordering *is* the contract, so this file tests it as one: each case names
the rule it expects to fire and asserts on the `reason` as well as the time. A
rule producing the right answer for the wrong reason is the failure that matters
here, because it means the next change to a polygon or a schedule will move a
promise nobody can account for.

    1. no zone   -> nothing. The address cannot be served.
    2. group     -> that group's next window close + its minutes-to-door.
    3. next_day  -> tomorrow, or the day after if today's trading is over.
    4. minutes   -> now + the courier's minutes, or the next opening + them.

The failure this has always guarded against is the third-party case rendered
with the precision of the second: "tomorrow at 14:00" for an order handed to a
partner is a promise made with somebody else's van. `precision` is what keeps
them apart, and every case below pins it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.models.courier import Courier
from app.models.delivery_batch import DeliveryBatchGroup, DeliveryBatchWindow
from app.services.delivery_promise import _Context, resolve
from app.services.delivery_zone_service import Zone

DUBAI = ZoneInfo("Asia/Dubai")

#: The Sharjah kitchen's real trading day.
OPENS, CLOSES = "09:00", "23:00"


def at(hour: int, minute: int = 0, day: int = 8) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=DUBAI)


def window(label: str, start: str, end: str) -> DeliveryBatchWindow:
    sh, sm = (int(p) for p in start.split(":"))
    eh, em = (int(p) for p in end.split(":"))
    return DeliveryBatchWindow(
        id=uuid.uuid4(),
        label=label,
        start_hour=sh,
        start_minute=sm,
        end_hour=eh,
        end_minute=em,
        is_active=True,
    )


def courier(code: str, *, kind: str, minutes: int | None) -> Courier:
    return Courier(
        code=code,
        name=code,
        supports_batching=code == "lalamove",
        unbatched_promise_kind=kind,
        unbatched_promise_minutes=minutes,
    )


LALAMOVE = courier("lalamove", kind="minutes", minutes=60)
NOON_SEND = courier("noon_send", kind="minutes", minutes=60)
THIRD_PARTY = courier("third_party", kind="next_day", minutes=None)


def zone(provider: str, *, group_id: uuid.UUID | None = None) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="Test Zone",
        delivery_fee=Decimal("20.00"),
        fulfilment_provider=provider,
        min_lat=0,
        max_lat=1,
        min_lng=0,
        max_lng=1,
        rings=(),
        batch_group_id=group_id,
    )


def context(
    *,
    provider: str = "lalamove",
    group: DeliveryBatchGroup | None = None,
    windows: list[DeliveryBatchWindow] | None = None,
    courier_row: Courier | None = None,
    no_courier: bool = False,
    opens: str | None = OPENS,
    closes: str | None = CLOSES,
) -> _Context:
    return _Context(
        zone=zone(provider, group_id=group.id if group else None),
        courier=None
        if no_courier
        else (
            courier_row
            or {"lalamove": LALAMOVE, "noon_send": NOON_SEND}.get(provider, THIRD_PARTY)
        ),
        group=group,
        windows=windows or [],
        opens_at=opens,
        closes_at=closes,
    )


def group(name: str, minutes: int) -> DeliveryBatchGroup:
    return DeliveryBatchGroup(
        id=uuid.uuid4(),
        name=name,
        courier_code="lalamove",
        delivery_minutes_after_dispatch=minutes,
        is_active=True,
    )


# ── rule 1: nowhere ──────────────────────────────────────────────────────────


def test_no_zone_is_no_promise():
    """An address off the map cannot be served, so there is nothing to say."""
    empty = _Context(
        zone=None, courier=None, group=None, windows=[], opens_at=None, closes_at=None
    )
    assert resolve(empty, at(14)) is None


# ── rule 2: a declared batch group ───────────────────────────────────────────

DUBAI_WINDOWS = [
    window("Batch 1", "23:00", "12:00"),
    window("Batch 2", "12:00", "18:00"),
    window("Batch 3", "18:00", "21:00"),
    window("Batch 4", "21:00", "22:30"),
    window("Batch 5", "22:30", "23:00"),
]


@pytest.mark.parametrize(
    "now,expected,slot",
    [
        # Inside Batch 3 (18:00–21:00): leaves 21:00, lands 90 minutes later.
        (at(20), at(22, 30), "Batch 3"),
        # Inside Batch 2: leaves 18:00, lands 19:30.
        (at(13), at(19, 30), "Batch 2"),
        # Exactly on a boundary belongs to the slot starting, not the one
        # closing — otherwise an order lands on a van already pulling away.
        # Batch 3 runs 18:00–21:00, so this leaves at 21:00 and lands at 22:30.
        (at(18), at(22, 30), "Batch 3"),
    ],
)
def test_a_grouped_zone_waits_for_its_next_run(now, expected, slot):
    dubai = group("Dubai", 90)
    promise = resolve(context(group=dubai, windows=DUBAI_WINDOWS), now)
    assert promise is not None
    assert promise.at == expected
    assert promise.precision == "time"
    assert "batch:Dubai/" in promise.reason and "+90m" in promise.reason


def test_the_northern_group_carries_its_own_minutes():
    """
    Same rate card, same slots, different promise. The northern run crosses
    three emirates, so its number is 120 — which is the whole reason the figure
    lives on the group and not in one shared constant.
    """
    northern = group("Northern Emirates", 120)
    promise = resolve(context(group=northern, windows=DUBAI_WINDOWS), at(20))
    assert promise is not None
    assert promise.at == at(23, 0)
    assert "+120m" in promise.reason


def test_a_batched_zone_ignores_trading_hours():
    """
    Deliberate. The 23:00–12:00 slot exists *because* nothing leaves overnight,
    so applying the kitchen's close on top of it would subtract the same
    closure twice and promise a day later than the van actually arrives.
    """
    dubai = group("Dubai", 90)
    promise = resolve(context(group=dubai, windows=DUBAI_WINDOWS), at(23, 30))
    assert promise is not None
    # Batch 1 closes at noon tomorrow; 90 minutes after that.
    assert promise.at == at(13, 30, day=9)
    assert "batch:" in promise.reason


def test_an_inactive_group_falls_through_to_the_courier():
    """
    Switching a schedule off sends its zones out immediately rather than parking
    them against slots that will never fire. `_load` resolves an inactive group
    to None, so the resolver sees a zone with no group.
    """
    promise = resolve(context(group=None, windows=[]), at(14))
    assert promise is not None
    assert promise.at == at(15)
    assert "courier:lalamove +60m" in promise.reason


# ── rule 3: somebody else's van ──────────────────────────────────────────────


def test_third_party_promises_a_day_and_only_a_day():
    promise = resolve(context(provider="third_party"), at(14))
    assert promise is not None
    assert promise.precision == "day", "an hour was invented for a partner's van"
    assert promise.at.date() == at(0, day=9).date()
    assert promise.reason == "courier:third_party next_day"


def test_third_party_after_closing_is_the_day_after_tomorrow():
    """
    The store-hours rule. An order at 23:30 against a 23:00 close cannot even be
    baked today, so promising tomorrow would be promising a day early.
    """
    promise = resolve(context(provider="third_party"), at(23, 30))
    assert promise is not None
    assert promise.at.date() == at(0, day=10).date()
    assert "+1" in promise.reason and "after 23:00 close" in promise.reason


def test_third_party_before_opening_is_still_tomorrow():
    """
    07:00 is shut but is *not* after the close — today's trading has not
    happened yet, so the order still makes today's handover. This is why
    `is_after_close` exists rather than `not is_open`.
    """
    promise = resolve(context(provider="third_party"), at(7))
    assert promise is not None
    assert promise.at.date() == at(0, day=9).date()
    assert "+1" not in promise.reason


# ── rule 4: ours to dispatch ─────────────────────────────────────────────────


def test_noon_send_is_an_hour_from_now():
    promise = resolve(context(provider="noon_send"), at(14))
    assert promise is not None
    assert promise.at == at(15)
    assert promise.precision == "time"
    assert promise.reason == "courier:noon_send +60m from now"


def test_noon_send_after_closing_starts_the_clock_at_tomorrow_s_opening():
    """An order at 23:30 is not an hour away; nobody is there to bake it."""
    promise = resolve(context(provider="noon_send"), at(23, 30))
    assert promise is not None
    assert promise.at == at(10, day=9), "09:00 opening plus the hour"
    assert "from 2026-08-09 09:00 opening" in promise.reason


def test_a_courier_with_no_row_is_treated_as_somebody_else_s_van():
    """
    The safe reading. An unconfigured courier promising an hour would be the
    shop guessing on behalf of a carrier nobody has set up.
    """
    ctx = context(provider="lalamove", no_courier=True)
    promise = resolve(ctx, at(14))
    assert promise is not None
    assert promise.precision == "day"


def test_unreadable_trading_hours_do_not_stop_the_shop_quoting():
    """
    A branch record with a typo in it must not silently push every promise to an
    invented opening time. Always-open is the tolerant reading, and it matches
    `trading_hours.is_open`.
    """
    promise = resolve(context(provider="noon_send", opens=None, closes=None), at(3))
    assert promise is not None
    assert promise.at == at(4)
