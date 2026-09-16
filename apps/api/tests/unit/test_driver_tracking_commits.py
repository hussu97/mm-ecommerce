"""F-COU-22: the live-driver sweep commits one booking at a time.

`refresh_live_drivers` reads a batch of live Lalamove bookings and, for each,
may write a corrected status, a new driver, or — through `_reconcile_ending`
→ `apply_webhook` — a whole terminal transition with the email and refund path
that rides on it. It used to persist the entire batch under a single commit at
the end, so a failure on the last booking would roll back the reconciled ending
of the first. It now commits after each booking (the same discipline
`reconcile_push_only_endings` already follows), and rolls one bad booking back
on its own without touching the rest.

These are behavioural, on a fake session that records commit/rollback events; the
provider round-trip inside `_refresh_one` is stubbed, because what is under test
is the transaction boundary around it, not the HTTP.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.couriers import lalamove_service
from app.services.delivery import driver_tracking


class _FakeSession:
    """Records commit/rollback order and hands back a preset batch of rows."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows
        self.events: list[str] = []

    async def execute(self, _stmt: object) -> SimpleNamespace:
        rows = list(self._rows)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


def _row(name: str) -> SimpleNamespace:
    # Only `courier_order_id` is read (for the log line); identity is by name here.
    return SimpleNamespace(name=name, courier_order_id=f"LM-{name}")


@pytest.mark.asyncio
async def test_each_booking_is_committed_on_its_own(monkeypatch):
    monkeypatch.setattr(lalamove_service, "is_enabled", lambda: True)
    rows = [_row("a"), _row("b"), _row("c")]
    db = _FakeSession(rows)

    async def _refresh_one(_db, delivery, *, at):  # noqa: ANN001
        return True

    monkeypatch.setattr(driver_tracking, "_refresh_one", _refresh_one)

    refreshed = await driver_tracking.refresh_live_drivers(db)

    assert refreshed == 3
    # One commit per booking, not one commit for the batch.
    assert db.events == ["commit", "commit", "commit"]


@pytest.mark.asyncio
async def test_one_failing_booking_does_not_discard_the_others(monkeypatch):
    monkeypatch.setattr(lalamove_service, "is_enabled", lambda: True)
    rows = [_row("a"), _row("boom"), _row("c")]
    db = _FakeSession(rows)

    async def _refresh_one(_db, delivery, *, at):  # noqa: ANN001
        if delivery.name == "boom":
            raise RuntimeError("courier read failed")
        return True

    monkeypatch.setattr(driver_tracking, "_refresh_one", _refresh_one)

    refreshed = await driver_tracking.refresh_live_drivers(db)

    # The good bookings each committed; the bad one rolled back on its own and
    # did not stop the third — its failure is isolated to its own transaction.
    assert refreshed == 2
    assert db.events == ["commit", "rollback", "commit"]
