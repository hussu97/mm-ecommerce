"""
"When will it arrive?" — the question every shopper actually has.

There are two honest answers and they come from very different places. In a zone
we dispatch ourselves, every term is a number we control: the order joins the
open window, that window closes at a time we set, and an hour later it is
through the door. In a third-party zone the van belongs to somebody whose
schedule we cannot see, and the only thing we can commit to is the next day.

The failure worth guarding against is the second case being rendered with the
precision of the first. "Tomorrow at 14:00" for an order we hand to a partner is
a promise made with someone else's van.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.delivery_batch import DeliveryBatchWindow
from app.services import delivery_service
from app.services.delivery_zone_service import Zone

DUBAI = ZoneInfo("Asia/Dubai")


def _window(label: str, start: str, end: str) -> DeliveryBatchWindow:
    sh, sm = (int(p) for p in start.split(":"))
    eh, em = (int(p) for p in end.split(":"))
    return DeliveryBatchWindow(
        label=label,
        start_hour=sh,
        start_minute=sm,
        end_hour=eh,
        end_minute=em,
        is_active=True,
    )


#: The shop's schedule as published by 059: opens at 23:00 the night before,
#: closes at 23:00, and tiles the whole day in between.
CITY_SCHEDULE = [
    _window("Batch 1", "23:00", "12:00"),
    _window("Batch 2", "12:00", "18:00"),
    _window("Batch 3", "18:00", "21:00"),
    _window("Batch 4", "21:00", "22:30"),
    _window("Batch 5", "22:30", "23:00"),
]


def _zone(*, lalamove: bool) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="Sharjah City" if lalamove else "Fujairah",
        delivery_fee=Decimal("15.00" if lalamove else "80.00"),
        fulfilment_provider="lalamove" if lalamove else "third_party",
        min_lat=24.0,
        max_lat=26.0,
        min_lng=54.0,
        max_lng=57.0,
        rings=(),
        free_delivery_eligible=lalamove,
    )


def dubai(hour: int, minute: int = 0, day: int = 4) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=DUBAI)


async def _estimate(zone: Zone | None, now: datetime, windows=CITY_SCHEDULE):
    with patch.object(
        delivery_service.batching_service,
        "active_windows",
        new=AsyncMock(return_value=windows),
    ):
        return await delivery_service.estimate_arrival(AsyncMock(), zone, now=now)


# ── zones we dispatch ourselves ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "ordered_at,arrives",
    [
        # Inside Batch 1, which closes at noon: delivered by 13:00 the same day.
        ((0, 30), (13, 0, 4)),
        ((9, 0), (13, 0, 4)),
        ((11, 59), (13, 0, 4)),
        # A boundary belongs to the window opening, so 12:00 waits for 18:00.
        ((12, 0), (19, 0, 4)),
        ((17, 30), (19, 0, 4)),
        ((18, 0), (22, 0, 4)),
        # Batch 4 closes at 22:30, not 23:00 — the half hour matters.
        ((21, 0), (23, 30, 4)),
        # Batch 5 closes at 23:00, the last dispatch before the shop shuts, so
        # its drops land just after midnight.
        ((22, 45), (0, 0, 5)),
    ],
)
async def test_a_city_pin_is_promised_its_batch_close_plus_an_hour(ordered_at, arrives):
    estimate = await _estimate(_zone(lalamove=True), dubai(*ordered_at))

    hour, minute, day = arrives
    assert estimate.precision == "time"
    assert estimate.at == dubai(hour, minute, day=day)


async def test_an_order_after_closing_joins_the_next_mornings_run():
    """
    23:00 is when the shop shuts and when Batch 1 opens. An order placed at
    23:30 has nobody to pack it tonight, so it rides the run that leaves at noon
    — which is exactly what the schedule was reshaped to express.
    """
    estimate = await _estimate(_zone(lalamove=True), dubai(23, 30))

    assert estimate.precision == "time"
    assert estimate.at == dubai(13, 0, day=5), "did not roll to the next day"


async def test_the_city_schedule_leaves_no_hour_unanswered():
    """A gap would mean an order with no promised time at all."""
    zone = _zone(lalamove=True)
    for hour in range(24):
        for minute in (0, 30):
            estimate = await _estimate(zone, dubai(hour, minute))
            assert estimate.precision == "time", f"{hour:02d}:{minute:02d} fell through"


async def test_a_city_zone_with_no_schedule_still_answers():
    """
    Dispatch does not wait when there is no window — the order goes out on its
    own immediately — so the estimate must not wait either.
    """
    estimate = await _estimate(_zone(lalamove=True), dubai(14, 0), windows=[])

    assert estimate.precision == "time"
    assert estimate.at == dubai(15, 0)


# ── third-party zones ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("hour", [0, 8, 12, 17, 22, 23])
async def test_a_third_party_pin_is_next_day_whatever_the_clock_says(hour):
    """
    Their van, their schedule. An order at nine in the morning is not more
    likely to go out today than one at eleven at night, because neither of them
    is ours to send.
    """
    estimate = await _estimate(_zone(lalamove=False), dubai(hour, 15))

    assert estimate.precision == "day"
    assert estimate.at.date() == dubai(hour).date() + timedelta(days=1)


async def test_a_third_party_estimate_names_no_hour():
    """
    A date with a time on it is a promise about a van we do not control. The
    precision flag is what stops the storefront rendering one.
    """
    estimate = await _estimate(_zone(lalamove=False), dubai(9, 42))

    assert estimate.precision == "day"
    assert (estimate.at.hour, estimate.at.minute) == (0, 0)


async def test_a_pin_outside_every_zone_is_treated_as_third_party():
    estimate = await _estimate(None, dubai(9, 0))

    assert estimate.precision == "day"


# ── it reaches the storefront ─────────────────────────────────────────────────


async def test_the_quote_carries_the_estimate():
    from app.models.delivery_settings import DeliverySettings

    settings = DeliverySettings(
        free_delivery_threshold=Decimal("150.00"),
        pickup_fee=Decimal("0.00"),
        default_delivery_fee=Decimal("80.00"),
    )
    with (
        patch.object(
            delivery_service, "get_settings", new=AsyncMock(return_value=settings)
        ),
        patch.object(
            delivery_service.delivery_zone_service,
            "find_zone",
            new=AsyncMock(return_value=_zone(lalamove=False)),
        ),
        patch.object(
            delivery_service.lalamove_service,
            "estimate_for_point",
            new=AsyncMock(return_value=(None, None)),
        ),
    ):
        result = await delivery_service.quote(
            AsyncMock(), Decimal("100.00"), latitude=25.1, longitude=56.3
        )

    assert result["delivery_estimate"]["precision"] == "day"
    assert result["delivery_estimate"]["at"]


async def test_there_is_no_estimate_before_there_is_a_pin():
    """Nothing to read a schedule off, so nothing is promised."""
    from app.models.delivery_settings import DeliverySettings

    settings = DeliverySettings(
        free_delivery_threshold=Decimal("150.00"),
        pickup_fee=Decimal("0.00"),
        default_delivery_fee=Decimal("80.00"),
    )
    with patch.object(
        delivery_service, "get_settings", new=AsyncMock(return_value=settings)
    ):
        result = await delivery_service.quote(AsyncMock(), Decimal("100.00"))

    assert result["delivery_estimate"] is None
