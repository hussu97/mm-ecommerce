"""
The seven digits a driver reads back.

`MM-20260805-007` is a fine database key and a poor thing to say down a phone.
noon Send asked for something shorter for their riders; the same argument won
Lalamove's driver notes. What this file guards is the two properties that make
it usable: it really is seven digits every time, and it never changes under an
order that has already been booked once.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.couriers import courier_reference


class _Result:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return SimpleNamespace(first=lambda: self._value)


class _Db:
    """
    A session that answers "is this number taken" from a set.

    `begin_nested` is the savepoint the real code writes inside, so the fake
    honours it as an async context manager and records every flush.
    """

    def __init__(self, taken: set[str] | None = None, *, collide_once: bool = False):
        self.taken = taken or set()
        self.collide_once = collide_once
        self.flushes = 0

    async def execute(self, _stmt):
        # The candidate is inside the statement's WHERE clause; comparing it
        # here would mean parsing SQL. Every test that needs a "taken" answer
        # sets `taken` to either nothing or everything, which is enough.
        return _Result(next(iter(self.taken), None))

    def begin_nested(self):
        class _Savepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        return _Savepoint()

    async def flush(self):
        self.flushes += 1
        if self.collide_once:
            self.collide_once = False
            raise IntegrityError("insert", {}, Exception("duplicate key"))


def _delivery(courier_reference: str | None = None):
    return SimpleNamespace(id=uuid.uuid4(), courier_reference=courier_reference)


def test_a_reference_is_always_seven_digits():
    for _ in range(200):
        value = courier_reference.generate()
        assert len(value) == 7
        assert value.isdigit()
        # No leading zero, so nothing downstream can lose one to a spreadsheet
        # or a courier's integer column.
        assert not value.startswith("0")


@pytest.mark.asyncio
async def test_it_draws_one_and_writes_it_down():
    db, delivery = _Db(), _delivery()
    value = await courier_reference.assign(db, delivery)
    assert value is not None
    assert delivery.courier_reference == value
    assert len(value) == 7


@pytest.mark.asyncio
async def test_a_redispatch_keeps_the_number_the_driver_was_given():
    """
    The reference identifies the order, not the booking. A second rider quoting
    it has to reach the same cake — and a customer told "quote 4820193" must not
    find that number belongs to nothing after a re-dispatch.
    """
    db, delivery = _Db(), _delivery("4820193")
    assert await courier_reference.assign(db, delivery) == "4820193"
    assert db.flushes == 0, "nothing to write"


@pytest.mark.asyncio
async def test_a_number_someone_else_took_first_is_given_up_on():
    """
    The read is a courtesy; the unique index is the guarantee. Two dispatches
    can pass the check with the same number, so the flush is allowed to fail and
    the next candidate is tried inside the same transaction.
    """
    db, delivery = _Db(collide_once=True), _delivery()
    value = await courier_reference.assign(db, delivery)
    assert value is not None
    assert delivery.courier_reference == value
    assert db.flushes == 2, "first one collided, second one stuck"


@pytest.mark.asyncio
async def test_a_full_space_gives_up_rather_than_blocking_the_dispatch():
    """
    Nine million numbers cannot really run out, but a caller must never be left
    waiting on one: the customer has paid and the box is packed. `None` means
    "send the order number this time", not "do not dispatch".
    """
    db, delivery = _Db(taken={"anything"}), _delivery()
    assert await courier_reference.assign(db, delivery) is None
    assert delivery.courier_reference is None
