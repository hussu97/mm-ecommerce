"""
When a terminal is allowed to accept an order without a person.

The flag on the device says *this counter has nobody watching it*. It stopped
being sufficient the moment acceptance began calling a driver: accepting now
prints the ticket and books the van in one act, so a website order placed at
03:00 on a shop that sells overnight would put a rider at a dark shutter within
fifteen minutes, next to a receipt nobody is standing beside.

So the device flag is a permission and this is the second condition on it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models.pos_order import OrderSourceEnum
from app.services.pos import pos_order_service

DUBAI = ZoneInfo("Asia/Dubai")


def _window(opens="09:00", closes="23:00") -> tuple[str, str]:
    """Today's resolved `(opens, closes)` — what `may_auto_accept` now takes in
    place of a branch, since hours live in the weekly schedule."""
    return (opens, closes)


def _order(placed_at: datetime, source=OrderSourceEnum.ONLINE.value):
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260815-001",
        source=source,
        created_at=placed_at,
        opened_at=None,
    )


def test_an_order_placed_in_hours_may_be_taken_by_the_terminal():
    placed = datetime(2026, 8, 15, 17, 0, tzinfo=DUBAI)
    assert pos_order_service.may_auto_accept(_order(placed), _window()) is True


def test_an_order_placed_overnight_waits_for_a_person():
    """The one this rule exists for: 03:00, shop shut, nobody at the counter."""
    placed = datetime(2026, 8, 15, 3, 0, tzinfo=DUBAI)
    assert pos_order_service.may_auto_accept(_order(placed), _window()) is False


def test_the_placement_time_decides_and_not_the_moment_of_asking():
    """
    An order placed at 03:00 is still not auto-acceptable when the shop opens at
    09:00. Six hours have passed, nobody has looked at it, and whether it can
    still be made by the promised time is a judgement — so the alarm rings and a
    person decides. The rule is a property of the order, which is what stops it
    flipping under a terminal at opening time.
    """
    overnight = _order(datetime(2026, 8, 15, 3, 0, tzinfo=DUBAI))
    assert pos_order_service.may_auto_accept(overnight, _window()) is False


def test_a_branch_trading_past_midnight_is_still_open_at_one():
    late = _window(opens="09:00", closes="02:00")
    placed = datetime(2026, 8, 15, 1, 0, tzinfo=DUBAI)
    assert pos_order_service.may_auto_accept(_order(placed), late) is True


def test_a_counter_order_is_never_in_question():
    """A cashier ringing one up in person is as accepted as an order gets."""
    placed = datetime(2026, 8, 15, 3, 0, tzinfo=DUBAI)
    counter = _order(placed, source="pos")
    assert pos_order_service.may_auto_accept(counter, _window()) is True


def test_hours_we_cannot_read_do_not_strand_the_order():
    """
    A branch that will not load is a database problem, not a shut shop. Refusing
    would leave the order sitting on a silent terminal; allowing it prints a
    ticket at a counter that is probably staffed — and the alarm rings either
    way now, so somebody sees it.
    """
    placed = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)
    assert pos_order_service.may_auto_accept(_order(placed), None) is True
