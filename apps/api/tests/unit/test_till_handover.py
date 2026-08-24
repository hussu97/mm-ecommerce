"""
Handing a shared terminal from one cashier to the next.

The trap this closes: `/tills/current` is scoped to the caller, so a cashier
signing in on an iPad somebody else left open is shown "Open your till" with no
till of their own — and opening one was refused, because the *device* already
had one. Close till lives behind an open till, so they could not clear it
either. The counter iPad was dead until the original cashier came back.

The drawer is counted once at a handover. That single count closes the outgoing
till (which is what gives it a variance and a Z report) and opens the incoming
one. These tests pin who gets handed over and who does not.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.pos import till_service


class _Result:
    def __init__(self, till):
        self._till = till

    def scalars(self):
        return self

    def first(self):
        return self._till


class _DB:
    """Answers the one query `open_till_on_device` makes."""

    def __init__(self, till=None):
        self._till = till

    async def execute(self, _stmt):
        return _Result(self._till)


def _user(**overrides):
    return SimpleNamespace(
        id=uuid.uuid4(),
        display_name="Aisha",
        email="aisha@example.com",
        is_admin=False,
        can=lambda _perm: False,
        **overrides,
    )


def _till(user_id):
    return SimpleNamespace(id=uuid.uuid4(), user_id=user_id)


@pytest.mark.asyncio
async def test_a_till_left_open_by_someone_else_is_closed_on_the_new_count(
    monkeypatch,
):
    """The count the incoming cashier types is what the old till closes on."""
    outgoing_owner = uuid.uuid4()
    outgoing = _till(outgoing_owner)
    incoming = _user()
    closed: dict = {}

    async def fake_close(db, *, till, closed_by, closing_amount, notes=None):
        closed.update(
            till=till, closed_by=closed_by, closing_amount=closing_amount, notes=notes
        )
        return till

    monkeypatch.setattr(till_service, "close_till", fake_close)

    handed = await till_service.handover_on_device(
        _DB(outgoing),
        device_id=uuid.uuid4(),
        user=incoming,
        counted=Decimal("350.00"),
    )

    assert handed is outgoing
    assert closed["till"] is outgoing
    assert closed["closed_by"] is incoming
    # The one count, doing both jobs.
    assert closed["closing_amount"] == Decimal("350.00")
    assert "Aisha" in closed["notes"]


@pytest.mark.asyncio
async def test_nothing_open_on_the_terminal_is_not_a_handover(monkeypatch):
    async def fail(*_args, **_kwargs):  # pragma: no cover — must not be reached
        raise AssertionError("closed a till that does not exist")

    monkeypatch.setattr(till_service, "close_till", fail)

    assert (
        await till_service.handover_on_device(
            _DB(None), device_id=uuid.uuid4(), user=_user(), counted=Decimal("0")
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_cashier_never_hands_over_to_themselves(monkeypatch):
    """
    Their own open till is a different problem, and closing it here would report
    their shift twice. `open_till` refuses it by name before we ever get here.
    """
    incoming = _user()

    async def fail(*_args, **_kwargs):  # pragma: no cover — must not be reached
        raise AssertionError("closed the caller's own till")

    monkeypatch.setattr(till_service, "close_till", fail)

    assert (
        await till_service.handover_on_device(
            _DB(_till(incoming.id)),
            device_id=uuid.uuid4(),
            user=incoming,
            counted=Decimal("100"),
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_terminal_with_no_device_id_has_nothing_to_hand_over():
    assert (
        await till_service.handover_on_device(
            _DB(_till(uuid.uuid4())),
            device_id=None,
            user=_user(),
            counted=Decimal("100"),
        )
        is None
    )
