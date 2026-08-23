"""
The email a change of courier leaves owing, and the three times it does not.

Emails hang off status transitions, so moving an order to a different courier
sends nothing by default. That is right almost everywhere and wrong in exactly
one direction, and the wrong one is silent: a customer who paid, heard "we've
got your order", and then hears nothing at all until a box arrives.

`should_send_packed` suppresses the packed email for a courier we book, because
the news worth having is the next event — a rider actually carrying the box.
Move that order onto a third party and the next event never comes, because
nobody reports back from a third-party zone. The suppressed email has to be sent
late rather than never.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services import email_service

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def _order(
    *,
    status=OrderStatusEnum.PACKED,
    courier_managed: bool,
    method=DeliveryMethodEnum.DELIVERY,
    number="MM-RE-1",
):
    """The bits of `OrderResponse` the packed decision actually reads."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number=number,
        status=status,
        delivery_method=method,
        fulfilment=SimpleNamespace(courier_managed=courier_managed),
    )


@pytest.fixture
def journal(monkeypatch):
    """The `email_logs` rows that exist, and what a repair tried to send."""
    state = {"sent_templates": set(), "sends": []}

    async def already_sent(order_number, template):
        return template.removesuffix(".html") in state["sent_templates"]

    async def send_packed(order):
        state["sends"].append(order.order_number)

    monkeypatch.setattr(email_service, "already_sent", already_sent)
    monkeypatch.setattr(email_service, "send_order_packed", send_packed)
    return state


@pytest.fixture
def loaded(monkeypatch):
    """Let `repair_after_reassignment` reach a response without a database."""
    holder = {}

    async def get_for_notification(db, order_id):
        return holder["order"]

    async def to_response(db, order):
        return holder["order"]

    from app.services.orders import order_service

    monkeypatch.setattr(order_service, "get_for_notification", get_for_notification)
    monkeypatch.setattr(order_service, "to_response", to_response)
    return holder


async def _repair(loaded, order):
    loaded["order"] = order
    return await email_service.repair_after_reassignment(None, order)


@pytest.mark.asyncio
async def test_moving_off_a_courier_sends_the_packed_email_that_was_suppressed(
    journal, loaded
):
    """The hole. Without this the customer hears nothing until the doorbell."""
    order = _order(courier_managed=False)
    assert await _repair(loaded, order) == "packed"
    assert journal["sends"] == ["MM-RE-1"]


@pytest.mark.asyncio
async def test_it_does_not_send_a_second_time(journal, loaded):
    """
    Idempotent against the journal rather than a flag on the order, because the
    journal is the record of what was actually sent and a flag would be a second
    answer to the same question.
    """
    journal["sent_templates"].add("order_packed")
    order = _order(courier_managed=False)
    assert await _repair(loaded, order) is None
    assert journal["sends"] == []


@pytest.mark.asyncio
async def test_an_order_still_on_a_courier_is_owed_nothing(journal, loaded):
    """
    noon Send to Lalamove, or the reverse. Packed was suppressed on both sides
    and `out_for_delivery` is the real news either way.
    """
    order = _order(courier_managed=True)
    assert await _repair(loaded, order) is None
    assert journal["sends"] == []


@pytest.mark.asyncio
async def test_an_order_that_has_not_been_packed_yet_is_owed_nothing(journal, loaded):
    """
    Moved at `confirmed` or `arrived_at_pos`. Packed has not happened, so the
    decision gets made later against whichever courier is carrying it then —
    which is the ordinary path, and already correct.
    """
    for status in (OrderStatusEnum.CONFIRMED, OrderStatusEnum.ARRIVED_AT_POS):
        order = _order(status=status, courier_managed=False)
        assert await _repair(loaded, order) is None
    assert journal["sends"] == []


@pytest.mark.asyncio
async def test_an_order_back_from_a_failed_handover_is_owed_one(journal, loaded):
    """
    A rider carried the box to a door and brought it back, which is about as
    packed as an order gets — and that customer has heard less than anybody.
    """
    order = _order(status=OrderStatusEnum.UNDELIVERED, courier_managed=False)
    assert await _repair(loaded, order) == "packed"


@pytest.mark.asyncio
async def test_a_mail_failure_never_fails_the_move(journal, loaded, monkeypatch):
    """
    A booking has been cancelled and another made by the time this runs. A mail
    server is not a reason to unmake either.
    """

    async def boom(order):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(email_service, "send_order_packed", boom)
    assert await _repair(loaded, _order(courier_managed=False)) is None


@pytest.mark.asyncio
async def test_the_journal_is_asked_for_the_name_it_actually_stores():
    """
    `_send_order_email` logs `template.removesuffix(".html")`, so the column
    holds `order_packed`. Asking for `order_packed.html` would match nothing —
    and a dedupe that never matches is a dedupe that is not there, which is
    invisible until a customer gets the same email twice.
    """
    asked: list[str] = []

    class _Result:
        def first(self):
            return None

    class _Db:
        async def execute(self, stmt):
            asked.append(str(stmt.compile().params.get("template_1")))
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    import app.services.email_service as mod

    original = mod.AsyncSessionFactory
    mod.AsyncSessionFactory = lambda: _Db()
    try:
        await mod.already_sent("MM-RE-1", mod.PACKED_TEMPLATE)
    finally:
        mod.AsyncSessionFactory = original

    assert asked == ["order_packed"], "the suffix has to come off"
