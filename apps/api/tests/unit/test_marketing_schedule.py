from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.marketing import (
    Discount,
    HouseAccount,
    Promotion,
    TimedEvent,
)


def event(from_time: int, to_time: int, **days) -> TimedEvent:
    row = TimedEvent()
    row.from_time = from_time
    row.to_time = to_time
    for index, name in enumerate(
        ["is_mon", "is_tue", "is_wed", "is_thu", "is_fri", "is_sat", "is_sun"]
    ):
        setattr(row, name, days.get(name, True))
        del index
    return row


def minutes(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


# ─── Time windows ─────────────────────────────────────────────────────────────


def test_window_inside_a_single_day():
    happy_hour = event(minutes(15), minutes(18))
    assert not happy_hour.runs_at(minutes(14, 59))
    assert happy_hour.runs_at(minutes(15))
    assert happy_hour.runs_at(minutes(16, 30))
    assert happy_hour.runs_at(minutes(18))
    assert not happy_hour.runs_at(minutes(18, 1))


def test_window_that_crosses_midnight():
    """
    A late-night offer running 22:00 → 02:00 must be live on both sides of
    midnight. Naive range comparison would make this window match nothing.
    """
    late = event(minutes(22), minutes(2))
    assert late.runs_at(minutes(22))
    assert late.runs_at(minutes(23, 59))
    assert late.runs_at(minutes(0))
    assert late.runs_at(minutes(1, 30))
    assert late.runs_at(minutes(2))
    assert not late.runs_at(minutes(3))
    assert not late.runs_at(minutes(12))


def test_all_day_window():
    all_day = event(0, 1439)
    assert all_day.runs_at(0)
    assert all_day.runs_at(minutes(12))
    assert all_day.runs_at(1439)


def test_day_of_week_uses_python_monday_zero():
    weekend = event(
        0, 1439, is_mon=False, is_tue=False, is_wed=False, is_thu=False, is_fri=False
    )
    assert not weekend.runs_on(0)  # Monday
    assert not weekend.runs_on(4)  # Friday
    assert weekend.runs_on(5)  # Saturday
    assert weekend.runs_on(6)  # Sunday


def test_promotion_shares_the_same_scheduling():
    promo = Promotion()
    promo.from_time = minutes(11)
    promo.to_time = minutes(15)
    for name in ["is_mon", "is_tue", "is_wed", "is_thu", "is_fri", "is_sat", "is_sun"]:
        setattr(promo, name, True)

    assert promo.runs_at(minutes(12))
    assert not promo.runs_at(minutes(16))


# ─── Discount targeting ───────────────────────────────────────────────────────


def _discount(branch_ids=None, order_types=None) -> Discount:
    row = Discount()
    row.branch_ids = branch_ids or []
    row.order_types = order_types or []
    row.is_active = True
    row.deleted_at = None
    return row


def test_empty_targeting_means_everywhere():
    import uuid

    discount = _discount()
    assert discount.applies_to(uuid.uuid4(), "delivery")
    assert discount.applies_to(None, None)


def test_branch_scoped_discount():
    import uuid

    allowed = uuid.uuid4()
    other = uuid.uuid4()
    discount = _discount(branch_ids=[allowed])
    assert discount.applies_to(allowed, "pickup")
    assert not discount.applies_to(other, "pickup")


def test_order_type_scoped_discount():
    import uuid

    discount = _discount(order_types=["delivery"])
    assert discount.applies_to(uuid.uuid4(), "delivery")
    assert not discount.applies_to(uuid.uuid4(), "dine_in")


def test_inactive_discount_never_applies():
    import uuid

    discount = _discount()
    discount.is_active = False
    assert not discount.applies_to(uuid.uuid4(), "pickup")


# ─── House account credit ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("limit", "balance", "available"),
    [
        ("1000.00", "0.00", "1000.00"),
        ("1000.00", "250.00", "750.00"),
        ("1000.00", "1000.00", "0.00"),
        # Over-limit accounts report negative headroom rather than clamping,
        # so the shortfall is visible instead of silently hidden.
        ("500.00", "600.00", "-100.00"),
    ],
)
def test_available_credit(limit, balance, available):
    account = HouseAccount()
    account.credit_limit = Decimal(limit)
    account.balance = Decimal(balance)
    assert account.available_credit == Decimal(available)
