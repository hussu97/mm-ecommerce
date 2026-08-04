"""
What happens to a run the courier would not take.

Before this, nothing did. A refusal parked the batch in `failed` and the packed
boxes inside it waited for somebody to open the admin screen — which, for the
22:30 and 23:00 runs, meant waiting until morning. The distinction that makes
retrying safe rather than merely hopeful is whether another attempt could
plausibly go the other way, so most of what follows is about that line: an empty
wallet is worth another go, an address the courier does not serve never is.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.models.delivery_batch import BatchStatusEnum, DeliveryBatch
from app.services import batching_service
from app.services.batching_service import (
    RETRY_BACKOFF,
    _retry_at,
    kitchen_is_open,
)
from app.services.providers.lalamove_provider import LalamoveError

DUBAI = ZoneInfo("Asia/Dubai")


def dubai(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=DUBAI)


def batch(**kwargs) -> DeliveryBatch:
    defaults = dict(
        id=uuid.uuid4(),
        polygon_id=uuid.uuid4(),
        dispatch_at=dubai(21, 0),
        status=BatchStatusEnum.PENDING.value,
        window_label="Batch 3",
        stop_count=0,
        attempt_count=0,
        next_attempt_at=None,
    )
    return DeliveryBatch(**{**defaults, **kwargs})


# ── the kitchen's hours ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "moment,expected",
    [
        (dubai(9, 0), True),
        (dubai(22, 59), True),
        (dubai(23, 0), False),
        (dubai(0, 30), False),
        # Half-open at both ends, same as a batch window: opening minute counts,
        # closing minute does not.
        (dubai(8, 0), True),
        (dubai(7, 59), False),
    ],
)
def test_whether_anyone_is_there_to_hand_the_boxes_over(moment, expected):
    assert kitchen_is_open(moment, "08:00", "23:00") is expected


@pytest.mark.parametrize("moment", [dubai(23, 30), dubai(1, 0)])
def test_a_kitchen_that_trades_past_midnight_is_still_open_after_it(moment):
    assert kitchen_is_open(moment, "09:00", "02:00") is True


def test_a_kitchen_that_trades_past_midnight_is_shut_in_the_morning():
    assert kitchen_is_open(dubai(3, 0), "09:00", "02:00") is False


@pytest.mark.parametrize(
    "hours", [("", "23:00"), ("nine", "23:00"), ("08:00", "25:00")]
)
def test_hours_nobody_can_read_are_treated_as_always_open(hours):
    # A typo in a branch record is not a reason to strand a run: a retry that
    # might be too late beats one that never happens.
    assert kitchen_is_open(dubai(3, 0), *hours) is True


# ── the ladder ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "attempts_so_far,delay",
    [(1, RETRY_BACKOFF[0]), (2, RETRY_BACKOFF[1]), (3, RETRY_BACKOFF[2])],
)
def test_each_wait_is_longer_than_the_last(attempts_so_far, delay):
    now = dubai(12, 0)
    assert _retry_at(batch(attempt_count=attempts_so_far), now) == now + delay


def test_the_ladder_runs_out():
    """Four attempts is the whole of it. After that a person has to look."""
    assert _retry_at(batch(attempt_count=len(RETRY_BACKOFF) + 1), dubai(12, 0)) is None


def test_nothing_is_booked_for_after_the_kitchen_has_shut():
    # 22:40 + 45 minutes lands at 23:25, when there is nobody to collect from.
    assert (
        _retry_at(
            batch(attempt_count=3), dubai(22, 40), opens_at="08:00", closes_at="23:00"
        )
        is None
    )


def test_a_retry_that_still_lands_before_closing_is_kept():
    assert _retry_at(
        batch(attempt_count=1), dubai(22, 40), opens_at="08:00", closes_at="23:00"
    ) == dubai(22, 45)


# ── what dispatch decides ─────────────────────────────────────────────────────


class _Delivery:
    """Enough of an OrderDelivery for dispatch to route and to blame."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.order = object()
        self.batch_id = None
        self.last_error = None
        self.courier_order_id = None


class _Session:
    async def execute(self, stmt):  # pragma: no cover — never reached
        raise AssertionError("dispatch should not be querying in these tests")

    async def get(self, _model, _pk):
        # The run reads its zone to find the kitchen it collects from. Nothing
        # here is about geography, and a zone with no branch resolves to the
        # configured pickup — which is what `resolve_pickup` is stubbed to be.
        return None

    async def flush(self):
        return None


class _Pickup:
    opens_at = "00:00"
    closes_at = "23:59"


def _arrange(
    monkeypatch,
    *,
    deliveries: list[_Delivery],
    book,
    enabled: bool = True,
    pickup: object | None = _Pickup(),
    drop_ok: bool = True,
) -> None:
    async def ready(db, batch_id):
        return deliveries

    async def resolve_pickup(db, branch_id=None):
        return pickup

    monkeypatch.setattr(batching_service, "_ready_deliveries", ready)
    monkeypatch.setattr(batching_service, "_book_chunk", book)
    monkeypatch.setattr(
        batching_service.lalamove_service, "is_enabled", lambda: enabled
    )
    monkeypatch.setattr(
        batching_service.lalamove_service, "resolve_pickup", resolve_pickup
    )
    monkeypatch.setattr(
        batching_service.lalamove_service,
        "build_drop",
        lambda order: (object(), None) if drop_ok else (None, "No pin on this address"),
    )


def _refuses(error: LalamoveError):
    async def book(db, b, pickup, chunk, *, part, parts):
        raise error

    return book


async def _books(db, b, pickup, chunk, *, part, parts):
    if b.courier_order_id is None:
        b.courier_order_id = f"LLM-{part}"
        b.cost_total = Decimal("62.00")
    return None


async def test_an_outage_brings_the_run_back_by_itself(monkeypatch):
    b = batch()
    _arrange(
        monkeypatch,
        deliveries=[_Delivery()],
        book=_refuses(LalamoveError("Service temporarily unavailable", status=503)),
    )

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.FAILED.value
    assert b.attempt_count == 1
    assert b.next_attempt_at is not None, "a courier outage is exactly what to retry"


async def test_an_empty_wallet_brings_the_run_back_by_itself(monkeypatch):
    """The failure the ladder exists for — it needs time and a person, not a fix."""
    b = batch()
    _arrange(
        monkeypatch,
        deliveries=[_Delivery()],
        book=_refuses(
            LalamoveError("Insufficient credit", error_id="ERR_INSUFFICIENT_CREDIT")
        ),
    )

    await batching_service.dispatch_batch(_Session(), b)

    assert b.next_attempt_at is not None


async def test_an_address_the_courier_will_not_serve_is_never_retried(monkeypatch):
    b = batch()
    _arrange(
        monkeypatch,
        deliveries=[_Delivery()],
        book=_refuses(
            LalamoveError("Out of service area", error_id="ERR_OUT_OF_SERVICE_AREA")
        ),
    )

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.FAILED.value
    assert b.next_attempt_at is None, (
        "the address is not going to become serviceable in five minutes; "
        "retrying it just spends courier calls to be told the same thing"
    )


async def test_a_run_with_no_usable_address_is_never_retried(monkeypatch):
    b = batch()
    _arrange(monkeypatch, deliveries=[_Delivery()], book=_books, drop_ok=False)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.FAILED.value
    assert b.next_attempt_at is None


async def test_no_courier_configured_is_never_retried(monkeypatch):
    b = batch()
    _arrange(monkeypatch, deliveries=[_Delivery()], book=_books, enabled=False)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.FAILED.value
    assert b.next_attempt_at is None, "settings do not change on their own"


async def test_no_pickup_branch_is_never_retried(monkeypatch):
    b = batch()
    _arrange(monkeypatch, deliveries=[_Delivery()], book=_books, pickup=None)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.next_attempt_at is None


async def test_a_run_that_goes_out_owes_nothing(monkeypatch):
    b = batch()
    _arrange(monkeypatch, deliveries=[_Delivery(), _Delivery()], book=_books)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.DISPATCHED.value
    assert b.next_attempt_at is None
    assert b.stop_count == 2


async def test_the_run_stops_trying_once_the_ladder_is_spent(monkeypatch):
    b = batch(attempt_count=len(RETRY_BACKOFF))
    _arrange(
        monkeypatch,
        deliveries=[_Delivery()],
        book=_refuses(LalamoveError("Still down", status=503)),
    )

    await batching_service.dispatch_batch(_Session(), b)

    assert b.attempt_count == len(RETRY_BACKOFF) + 1
    assert b.next_attempt_at is None


async def test_an_attempt_clears_the_time_that_summoned_it(monkeypatch):
    """
    Otherwise a process dying mid-booking leaves a row every worker re-picks on
    every sweep, hammering the courier with the run that just killed it.
    """
    b = batch(next_attempt_at=dubai(20, 0))
    _arrange(monkeypatch, deliveries=[_Delivery()], book=_books)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.next_attempt_at is None


# ── half a run ────────────────────────────────────────────────────────────────


async def test_a_half_booked_run_keeps_trying_for_the_rest(monkeypatch):
    """
    Sixteen drops is two courier orders, and the second one can fail on its own.
    That used to read as a clean dispatch while five boxes sat in the kitchen.
    """
    calls = {"n": 0}

    async def book(db, b, pickup, chunk, *, part, parts):
        calls["n"] += 1
        if calls["n"] == 1:
            return await _books(db, b, pickup, chunk, part=part, parts=parts)
        raise LalamoveError("Service temporarily unavailable", status=503)

    b = batch()
    _arrange(monkeypatch, deliveries=[_Delivery() for _ in range(16)], book=book)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.DISPATCHED.value, "a driver really is coming"
    assert b.stop_count == 15
    assert b.next_attempt_at is not None, "the other order is still in the kitchen"


async def test_a_retry_adds_to_the_first_booking_rather_than_replacing_it(monkeypatch):
    """
    The second attempt starts counting parts from zero again. Keyed on the part
    number, it would overwrite the first courier order's id, its cost and the
    time the run left with the straggler's.
    """
    left_at = dubai(21, 5)
    b = batch(
        status=BatchStatusEnum.DISPATCHED.value,
        courier_order_id="LLM-first",
        cost_total=Decimal("62.00"),
        dispatched_at=left_at,
        stop_count=15,
        attempt_count=1,
        next_attempt_at=dubai(21, 10),
    )
    _arrange(monkeypatch, deliveries=[_Delivery()], book=_books)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.courier_order_id == "LLM-first"
    assert b.dispatched_at == left_at
    assert b.stop_count == 16, (
        "the drop already on the road was counted out of existence"
    )
    assert b.next_attempt_at is None


async def test_a_failed_retry_does_not_recall_the_driver_who_has_the_rest(monkeypatch):
    """
    The sweep marks every run it claims `dispatching`, including a half-booked
    one it is coming back to. If the retry fails and nothing puts the status
    back, a run with a driver on it reads as failed — or worse, is left stuck on
    `dispatching` once the ladder runs out.
    """
    b = batch(
        status=BatchStatusEnum.DISPATCHING.value,
        courier_order_id="LLM-first",
        stop_count=15,
        attempt_count=1,
    )
    _arrange(
        monkeypatch,
        deliveries=[_Delivery()],
        book=_refuses(LalamoveError("Still down", status=503)),
    )

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.DISPATCHED.value
    assert b.stop_count == 15, "the drops on the road are still on the road"
    assert b.next_attempt_at is not None


async def test_a_dispatched_run_is_not_cancelled_when_its_stragglers_are(monkeypatch):
    """
    `_ready_deliveries` returning nothing normally means an empty run. On a
    retry it means the leftovers were cancelled while it waited — and calling
    that "cancelled" would be a lie about a van already on the road.
    """
    b = batch(
        status=BatchStatusEnum.DISPATCHING.value,
        courier_order_id="LLM-first",
        stop_count=15,
        attempt_count=1,
    )
    _arrange(monkeypatch, deliveries=[], book=_books)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.DISPATCHED.value
    assert b.stop_count == 15


async def test_an_empty_run_that_never_went_anywhere_is_cancelled(monkeypatch):
    b = batch()
    _arrange(monkeypatch, deliveries=[], book=_books)

    await batching_service.dispatch_batch(_Session(), b)

    assert b.status == BatchStatusEnum.CANCELLED.value


# ── what the sweep asks for ───────────────────────────────────────────────────


async def test_the_sweep_collects_retries_as_well_as_new_runs():
    """
    One query, keyed on a time rather than on a status. A retry is not a
    different kind of work — it is the same run at a later minute.
    """
    seen: dict = {}

    class _Sweep:
        async def execute(self, stmt):
            seen["sql"] = str(stmt)

            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

        async def commit(self):
            return None

    assert await batching_service.dispatch_due_batches(_Sweep()) == []
    assert "next_attempt_at" in seen["sql"]
    # Two workers must not both book the same run. (`SKIP LOCKED` only renders
    # under the Postgres dialect, so the generic string shows `FOR UPDATE`.)
    assert "FOR UPDATE" in seen["sql"]


async def test_a_sweep_that_blows_up_still_schedules_another_go(monkeypatch):
    """A bug or a database wobble is not a reason to abandon packed boxes."""
    b = batch()

    class _Sweep:
        async def execute(self, stmt):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return [b]

            return _R()

        async def commit(self):
            return None

    async def explode(db, batch_):
        batch_.attempt_count += 1
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(batching_service, "dispatch_batch", explode)

    await batching_service.dispatch_due_batches(_Sweep())

    assert b.status == BatchStatusEnum.FAILED.value
    assert b.next_attempt_at is not None
    assert b.next_attempt_at - datetime.now(timezone.utc) < timedelta(minutes=6)
