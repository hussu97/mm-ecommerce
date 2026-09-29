from __future__ import annotations

from app.models.marketing import Promotion


def event(from_time: int, to_time: int, **days) -> Promotion:
    row = Promotion()
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


# ─── Promotion targeting ──────────────────────────────────────────────────────


def _promotion(sources=None, branch_ids=None, order_types=None) -> Promotion:
    row = Promotion()
    row.sources = sources or []
    row.branch_ids = branch_ids or []
    row.order_types = order_types or []
    row.is_active = True
    row.deleted_at = None
    return row


def _matches(promo: Promotion, branch_id, order_type, source="cashier") -> bool:
    return promo.matches_order(
        source=source, branch_id=branch_id, order_type=order_type
    )


def test_empty_targeting_means_everywhere():
    import uuid

    promo = _promotion()
    assert _matches(promo, uuid.uuid4(), "delivery")
    assert _matches(promo, None, None, source=None)


def test_branch_scoped_promotion():
    import uuid

    allowed = uuid.uuid4()
    other = uuid.uuid4()
    promo = _promotion(branch_ids=[allowed])
    assert _matches(promo, allowed, "pickup")
    assert not _matches(promo, other, "pickup")


def test_order_type_scoped_promotion():
    import uuid

    promo = _promotion(order_types=["delivery"])
    assert _matches(promo, uuid.uuid4(), "delivery")
    assert not _matches(promo, uuid.uuid4(), "dine_in")


def test_source_scoped_promotion():
    import uuid

    promo = _promotion(sources=["cashier"])
    assert _matches(promo, uuid.uuid4(), "pickup", source="cashier")
    assert not _matches(promo, uuid.uuid4(), "pickup", source="online")


def test_inactive_promotion_never_applies():
    import uuid

    promo = _promotion()
    promo.is_active = False
    assert not _matches(promo, uuid.uuid4(), "pickup")
