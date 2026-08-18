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


def courier(code: str, *, kind: str, minutes: int | None, days: int = 1) -> Courier:
    return Courier(
        code=code,
        name=code,
        supports_batching=code == "lalamove",
        unbatched_promise_kind=kind,
        unbatched_promise_minutes=minutes,
        unbatched_promise_days=days,
    )


LALAMOVE = courier("lalamove", kind="minutes", minutes=60)
NOON_SEND = courier("noon_send", kind="minutes", minutes=90)
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
    closed: frozenset[str] = frozenset(),
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
        closed_dates=closed,
    )


def shut(*days: int) -> frozenset[str]:
    """Those days of August 2026, closed."""
    return frozenset(f"2026-08-{day:02d}" for day in days)


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
    assert promise.reason == "courier:third_party next_day handover 2026-08-08 +1d"


def test_third_party_after_closing_is_the_day_after_tomorrow():
    """
    The store-hours rule. An order at 23:30 against a 23:00 close cannot even be
    baked today, so promising tomorrow would be promising a day early.
    """
    promise = resolve(context(provider="third_party"), at(23, 30))
    assert promise is not None
    assert promise.at.date() == at(0, day=10).date()
    assert "handover 2026-08-09" in promise.reason
    assert "after 23:00 close" in promise.reason


def test_third_party_before_opening_is_still_tomorrow():
    """
    07:00 is shut but is *not* after the close — today's trading has not
    happened yet, so the order still makes today's handover. This is why
    `is_after_close` exists rather than `not is_open`.
    """
    promise = resolve(context(provider="third_party"), at(7))
    assert promise is not None
    assert promise.at.date() == at(0, day=9).date()
    assert "after" not in promise.reason


def test_a_couriers_days_are_its_own_and_come_from_its_row():
    """
    The number that used to be the literal `1`. A partner covering Al Ain
    quotes two days on the same map, and moving it must not be a deploy.
    """
    slow = courier("third_party", kind="next_day", minutes=None, days=3)
    promise = resolve(context(provider="third_party", courier_row=slow), at(14))
    assert promise is not None
    assert promise.at.date() == at(0, day=11).date()
    assert "+3d" in promise.reason


def test_a_zero_day_courier_still_promises_tomorrow():
    """
    A belt to the column's braces. Zero would promise a day already half gone
    and a negative would promise one already spent; "next day" has always meant
    at least one, whatever a bad row says.
    """
    broken = courier("third_party", kind="next_day", minutes=None, days=0)
    promise = resolve(context(provider="third_party", courier_row=broken), at(14))
    assert promise is not None
    assert promise.at.date() == at(0, day=9).date()


# ── rule 4: ours to dispatch ─────────────────────────────────────────────────


def test_noon_send_is_its_own_minutes_from_now():
    """
    Ninety, not sixty, and not because this file says so — because the courier
    row says so. The number moved on 2026-08-18 and moving it did not touch
    this rule, which is the whole reason it is a column.
    """
    promise = resolve(context(provider="noon_send"), at(14))
    assert promise is not None
    assert promise.at == at(15, 30)
    assert promise.precision == "time"
    assert promise.reason == "courier:noon_send +90m from now"


def test_noon_send_after_closing_starts_the_clock_at_tomorrow_s_opening():
    """An order at 23:30 is not ninety minutes away; nobody is there to bake it."""
    promise = resolve(context(provider="noon_send"), at(23, 30))
    assert promise is not None
    assert promise.at == at(10, 30, day=9), "09:00 opening plus the minutes"
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
    assert promise.at == at(4, 30)


# ── holidays: one rule, three routes ─────────────────────────────────────────
#
# "A promise never names a day the branch is shut." Each rule reaches it
# differently, which is exactly why each needs its own case — a shared helper
# passing them all would prove the helper, not the rules.


def test_a_run_that_would_leave_on_a_holiday_waits_for_the_next_open_day():
    """
    Rule 2. Nothing is packed on a holiday, so no slot on it fires. The order
    joins the first run of the next day the kitchen works — found by asking the
    dispatcher's own matcher again from the top of that day, rather than by a
    second, differently-shaped idea of which slot is next.
    """
    dubai = group("Dubai", 90)
    promise = resolve(
        context(group=dubai, windows=DUBAI_WINDOWS, closed=shut(8)), at(13)
    )
    assert promise is not None
    # Batch 1 runs 23:00–12:00, so the first run of the 9th closes at noon.
    assert promise.at == at(13, 30, day=9)
    assert promise.precision == "time"
    assert "batch:Dubai/Batch 1" in promise.reason


def test_a_run_steps_over_consecutive_holidays():
    """A two-day Eid, not just a single day. The step repeats, it does not
    assume one."""
    dubai = group("Dubai", 90)
    promise = resolve(
        context(group=dubai, windows=DUBAI_WINDOWS, closed=shut(8, 9, 10)), at(13)
    )
    assert promise is not None
    assert promise.at == at(13, 30, day=11)


def test_a_run_on_an_open_day_is_untouched_by_a_later_holiday():
    """
    The ordinary case has to stay ordinary. A closure next week must not move
    a promise for a van leaving this afternoon.
    """
    dubai = group("Dubai", 90)
    with_holiday = resolve(
        context(group=dubai, windows=DUBAI_WINDOWS, closed=shut(20)), at(20)
    )
    without = resolve(context(group=dubai, windows=DUBAI_WINDOWS), at(20))
    assert with_holiday is not None and without is not None
    assert with_holiday.at == without.at == at(22, 30)
    assert with_holiday.reason == without.reason


def test_a_third_party_hands_over_on_the_first_day_the_kitchen_works():
    """
    Rule 3, first half. The closure moves the *handover* — the day the box can
    physically be put in somebody's van — and the courier's days are counted
    from there.
    """
    promise = resolve(context(provider="third_party", closed=shut(8, 9)), at(14))
    assert promise is not None
    assert promise.precision == "day"
    assert promise.at.date() == at(0, day=11).date()
    assert "handover 2026-08-10" in promise.reason
    assert "closed 2026-08-08→2026-08-10" in promise.reason


def test_a_third_party_arrival_is_walked_off_a_closed_day_too():
    """
    Rule 3, second half. The handover day is fine — the shop is open on the 8th
    — but the day the parcel would land is not, and a promise naming a day the
    shop is dark is one nobody there can answer the phone about.
    """
    promise = resolve(context(provider="third_party", closed=shut(9)), at(14))
    assert promise is not None
    assert promise.at.date() == at(0, day=10).date()
    assert "handover 2026-08-08" in promise.reason
    assert "closed 2026-08-09→2026-08-10" in promise.reason


def test_a_holiday_and_an_after_close_order_compound():
    """
    Both shifts apply, in order: 23:30 misses today's handover, and tomorrow is
    shut, so the box goes out on the 10th and lands on the 11th.
    """
    promise = resolve(context(provider="third_party", closed=shut(9)), at(23, 30))
    assert promise is not None
    assert promise.at.date() == at(0, day=11).date()
    assert "after 23:00 close" in promise.reason
    assert "handover 2026-08-10" in promise.reason


def test_noon_send_on_a_holiday_starts_its_clock_at_the_next_opening():
    """
    Rule 4, and it needed no code of its own: `is_open` is already false on a
    closed day, so the promise takes the branch it has always taken for a shut
    kitchen — and `next_opening` steps over the closure on its way.
    """
    promise = resolve(context(provider="noon_send", closed=shut(8)), at(14))
    assert promise is not None
    assert promise.at == at(10, 30, day=9), "09:00 opening plus the courier's 90"
    assert "from 2026-08-09 09:00 opening" in promise.reason
    assert "shut for the day" in promise.reason, (
        "read as though the shop had merely closed early"
    )


def test_noon_send_steps_over_a_run_of_holidays():
    promise = resolve(context(provider="noon_send", closed=shut(8, 9, 10)), at(14))
    assert promise is not None
    assert promise.at == at(10, 30, day=11)


def test_a_holiday_shuts_a_branch_whose_hours_cannot_be_read():
    """
    The tolerant reading of unparseable hours does not extend to a closure. A
    typo in `opening_from` is a guess; a date somebody wrote down is not.
    """
    promise = resolve(
        context(provider="noon_send", opens=None, closes=None, closed=shut(8)), at(14)
    )
    assert promise is not None
    assert promise.at == at(1, 30, day=9), "midnight on the next open day, plus 90"


def test_a_holiday_closes_the_small_hours_that_belong_to_it():
    """
    A branch trading 09:00–02:00 is still working the 8th's shift at 01:00 on
    the 9th. So a holiday on the 9th must not shut that tail — and must shut
    the tail that runs into the 10th, which is the 9th's own.

    Getting this backwards is the subtle failure: the promise would come out
    plausible and wrong by a day, in the direction nobody checks.
    """
    late = {"opens": "09:00", "closes": "02:00"}

    # 01:00 on the 9th is the *8th's* shift, which the 9th's holiday does not
    # touch. Still ninety minutes from now.
    still_working = resolve(
        context(provider="noon_send", closed=shut(9), **late),
        datetime(2026, 8, 9, 1, 0, tzinfo=DUBAI),
    )
    assert still_working is not None
    assert still_working.at == datetime(2026, 8, 9, 2, 30, tzinfo=DUBAI), (
        "the 9th's holiday reached back into the 8th's shift"
    )

    # 01:00 on the 10th is the 9th's shift, and the 9th is the holiday — so
    # there is nobody there, and the clock starts at the 10th's own opening.
    on_the_holiday = resolve(
        context(provider="noon_send", closed=shut(9), **late),
        datetime(2026, 8, 10, 1, 0, tzinfo=DUBAI),
    )
    assert on_the_holiday is not None
    assert on_the_holiday.at == datetime(2026, 8, 10, 10, 30, tzinfo=DUBAI), (
        "the 9th's holiday let its own small hours keep trading"
    )
