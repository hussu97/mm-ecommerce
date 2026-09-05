"""
What happens to a *single* order the courier would not take.

There used to be no answer at all: the `packed` transition that first tried an
order had already happened, and nothing was ever going to try again. The retry
ladder on `courier_service` is that answer — every order dispatches on its own
now, so a refusal on any of them climbs the same rungs.

MM-20260815-001 is why this file exists. Packed 11:21 on 15 Aug, `last_error =
"Courier is not configured; dispatch this order by hand"` — an error a deploy
fixed at 11:47. It sat there until somebody looked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.models.order import OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services.couriers import courier_service
from app.services.couriers.courier_service import RETRY_BACKOFF

NOW = datetime(2026, 8, 15, 7, 21, tzinfo=timezone.utc)  # 11:21 in Dubai


class _Db:
    """Serves the branch's weekly schedule `_record_outcome` resolves the retry
    window from. `window` is the same shift every weekday, or None for a branch
    with no schedule (which reads as all-day)."""

    def __init__(self, window: tuple[str, str] | None = None):
        self._window = window

    async def get(self, _model, _pk):
        return None

    async def flush(self):
        return None

    async def execute(self, _statement):
        rows = (
            [
                SimpleNamespace(
                    weekday=wd, opens=self._window[0], closes=self._window[1]
                )
                for wd in range(7)
            ]
            if self._window
            else []
        )
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _order(status=OrderStatusEnum.PACKED, branch_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260815-001",
        status=status,
        branch_id=branch_id,
    )


def _delivery(**overrides) -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="lalamove",
        zone_name="Dubai Near",
        fee_charged=Decimal("20.00"),
        dispatch_attempts=0,
    )
    for key, value in overrides.items():
        setattr(delivery, key, value)
    return delivery


# ── the ladder ────────────────────────────────────────────────────────────────


async def test_a_refusal_schedules_the_first_rung():
    delivery = _delivery(
        last_error="Courier is not configured; dispatch this order by hand"
    )

    await courier_service._record_outcome(_Db(), _order(), delivery)

    assert delivery.dispatch_attempts == 1
    assert delivery.next_attempt_at is not None
    # Five minutes, give or take the moment the test ran.
    gap = delivery.next_attempt_at - datetime.now(timezone.utc)
    assert RETRY_BACKOFF[0] - timedelta(seconds=5) < gap <= RETRY_BACKOFF[0]


async def test_the_ladder_runs_out_and_leaves_it_for_a_person():
    """
    Three rungs, then silence. A failure that has survived an hour of retries is
    not the kind that fixes itself, and a queue that keeps promising to try
    again is one nobody reads.
    """
    delivery = _delivery(last_error="Wallet is empty")

    for expected in range(1, len(RETRY_BACKOFF) + 1):
        await courier_service._record_outcome(_Db(), _order(), delivery)
        assert delivery.dispatch_attempts == expected
        assert delivery.next_attempt_at is not None

    await courier_service._record_outcome(_Db(), _order(), delivery)
    assert delivery.dispatch_attempts == len(RETRY_BACKOFF) + 1
    assert delivery.next_attempt_at is None, "the last rung stops the ladder"


async def test_a_retry_is_not_scheduled_into_a_shut_shop():
    """
    The driver collects from a physical counter. A booking made for 00:05,
    because something failed at 23:50, sends somebody to a dark shutter — so
    that one waits for the morning and a human, which is what would have
    happened anyway.
    """
    delivery = _delivery(last_error="Courier rejected the booking")

    await courier_service._record_outcome(
        _Db(window=("09:00", "23:00")), _order(branch_id=uuid.uuid4()), delivery
    )

    # `_retry_at` measures from now; the test's own clock decides whether five
    # minutes from now is inside 09:00–23:00, so assert on the rule rather than
    # on a fixed answer.
    from app.core import trading_hours

    five_on = datetime.now(timezone.utc) + RETRY_BACKOFF[0]
    if trading_hours.is_open(five_on, "09:00", "23:00"):
        assert delivery.next_attempt_at is not None
    else:
        assert delivery.next_attempt_at is None


async def test_a_booking_that_worked_clears_the_counter():
    """
    Otherwise the *next* failure — weeks later, a different problem — starts
    three rungs up and gives up almost immediately.
    """
    delivery = _delivery(
        dispatch_attempts=2,
        next_attempt_at=NOW,
        courier_order_id="3562487018653180622",
        last_error=None,
    )

    # Already packed, so `stamp_packed` stops at its transition guard rather
    # than reaching for a database this test does not have.
    await courier_service._record_outcome(_Db(), _order(), delivery)

    assert delivery.dispatch_attempts == 0
    assert delivery.next_attempt_at is None


async def test_a_dispatch_that_did_nothing_schedules_nothing():
    """
    Re-dispatching an order already out with a driver returns the row untouched:
    no booking made, no error written. That is not a failure and must not climb
    the ladder.
    """
    delivery = _delivery(
        courier_order_id="3562487018653180622",
        courier_status="ON_GOING",
        dispatch_attempts=0,
        last_error=None,
    )
    delivery.courier_order_id = None  # neither booked here nor failed
    await courier_service._record_outcome(_Db(), _order(), delivery)

    assert delivery.dispatch_attempts == 0
    assert delivery.next_attempt_at is None


# ── what the admin sees ───────────────────────────────────────────────────────


def test_an_order_the_sweep_still_owns_is_not_on_the_needs_a_human_list():
    """
    Every attempt writes `last_error`, so without this the list fills with rows
    that are resolving themselves and people stop reading it. It reappears the
    moment the ladder runs out — which is exactly when a person is the only
    thing left.
    """
    waiting = _delivery(last_error="Wallet is empty", next_attempt_at=NOW)
    assert waiting.is_awaiting_retry is True
    assert waiting.needs_attention is False

    exhausted = _delivery(last_error="Wallet is empty", next_attempt_at=None)
    assert exhausted.is_awaiting_retry is False
    assert exhausted.needs_attention is True


def test_an_order_already_booked_is_never_awaiting_a_retry():
    booked = _delivery(courier_order_id="355544", next_attempt_at=NOW)
    assert booked.is_awaiting_retry is False
