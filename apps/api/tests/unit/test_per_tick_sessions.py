"""F-OPS-5: the restructured sweeps never hold a scheduler session across a
third-party HTTP call.

`test_scheduler_session_guard` proves the *shape* structurally, by reading the
source. These prove the *behaviour*: each restructured loop is run with an
instrumented `SchedulerSessionFactory` that counts how many sessions are open at
any instant, and the provider it calls is spied so it records that count the
moment it is entered. The read must have committed and closed BEFORE the HTTP
(the count is 0 while the provider runs), and the result must land on a session
reopened AFTER it (the count is 1 again while the row is recorded).

The two loops these cover — `driver_routing.refresh_routes` and
`grubops_reconcile._reconcile_branch` — are the two whose per-item work is a
single discrete provider call bracketed by a DB read and a DB write, so a clean
read → close → HTTP → reopen split was possible. The loops that interleave a
provider call with DB writes inside a leaf shared with the request/webhook path
(courier dispatch's money lock, driver_tracking, grubops order ingest's APNS
push, the aggregator hours writers' portal-session load) are deferred and are
not asserted here; see the guard test's `_KNOWN_NESTED_HOLDS` backlog.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.branch import Branch
from app.models.order import Order
from app.models.order_delivery import OrderDelivery

NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


class _Tracker:
    """Shared state for one loop run: how many fake sessions are open now, the
    open/close/commit event log, and the count each spied provider saw."""

    def __init__(self) -> None:
        self.open = 0
        self.opened_total = 0
        self.events: list[str] = []
        self.seen_open_at_http: list[int] = []
        self.get_target: object | None = None
        self.rows: list[object] = []


class _FakeSession:
    """A session that touches no database but moves the tracker's open counter,
    so a spy can tell whether one is checked out at the moment it runs."""

    def __init__(self, tracker: _Tracker) -> None:
        self._t = tracker

    async def __aenter__(self) -> _FakeSession:
        self._t.open += 1
        self._t.opened_total += 1
        self._t.events.append("open")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._t.open -= 1
        self._t.events.append("close")
        return False

    async def execute(self, _stmt: object) -> SimpleNamespace:
        rows = list(self._t.rows)
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: rows, first=lambda: rows[0] if rows else None
            )
        )

    async def get(self, _model: object, _pk: object) -> object | None:
        return self._t.get_target

    def add(self, _row: object) -> None:  # pragma: no cover — unused here
        pass

    async def flush(self) -> None:  # pragma: no cover — unused here
        pass

    async def commit(self) -> None:
        self._t.events.append("commit")


def _factory(tracker: _Tracker):
    return lambda: _FakeSession(tracker)


def _branch() -> Branch:
    return Branch(
        id=uuid.uuid4(),
        name="Al Majaz",
        latitude=Decimal("25.3200"),
        longitude=Decimal("55.3800"),
    )


def _routed_delivery() -> OrderDelivery:
    delivery = OrderDelivery(
        order_id=uuid.uuid4(),
        provider="lalamove",
        courier_order_id="3463513590991397204",
        courier_status="ON_GOING",
        driver_assignment_count=0,
        driver_id="79973",
        driver_name="Ali",
        driver_latitude=Decimal("25.3500"),
        driver_longitude=Decimal("55.3900"),
        driver_location_at=NOW,
    )
    delivery.id = uuid.uuid4()
    order = Order()
    order.branch = _branch()
    delivery.order = order
    return delivery


# ── driver_routing.refresh_routes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_routes_calls_mapbox_with_no_session_held(monkeypatch):
    from app.services.delivery import driver_routing
    from app.services.providers import mapbox_provider

    tracker = _Tracker()
    delivery = _routed_delivery()
    tracker.rows = [delivery]
    tracker.get_target = delivery

    monkeypatch.setattr(mapbox_provider, "is_configured", lambda: True)
    monkeypatch.setattr(driver_routing, "SchedulerSessionFactory", _factory(tracker))

    async def _route(**_kwargs):
        # The instant Mapbox is entered, the read session must already be gone.
        tracker.seen_open_at_http.append(tracker.open)
        return mapbox_provider.Route(distance_km=3.2, minutes=7.5)

    monkeypatch.setattr(mapbox_provider, "route", _route)

    routed = await driver_routing.refresh_routes(now=NOW)

    assert routed == 1
    # The provider ran with zero sessions checked out.
    assert tracker.seen_open_at_http == [0]
    # The read opened and closed before the first thing the loop did after it.
    assert tracker.events[:2] == ["open", "close"]
    # The answer was persisted on a session reopened AFTER the call, and committed.
    assert delivery.driver_route_km == Decimal("3.2")
    assert delivery.driver_route_minutes == Decimal("7.5")
    assert delivery.driver_route_at == NOW
    assert "commit" in tracker.events[2:]
    # Every session that opened was closed — nothing left checked out.
    assert tracker.open == 0
    assert tracker.opened_total == 2  # one to read, one to persist


@pytest.mark.asyncio
async def test_refresh_routes_reopens_no_session_when_mapbox_says_nothing(monkeypatch):
    """A failed route writes nothing, so no second session is even opened."""
    from app.services.delivery import driver_routing
    from app.services.providers import mapbox_provider

    tracker = _Tracker()
    delivery = _routed_delivery()
    tracker.rows = [delivery]

    monkeypatch.setattr(mapbox_provider, "is_configured", lambda: True)
    monkeypatch.setattr(driver_routing, "SchedulerSessionFactory", _factory(tracker))

    async def _no_route(**_kwargs):
        tracker.seen_open_at_http.append(tracker.open)
        return None

    monkeypatch.setattr(mapbox_provider, "route", _no_route)

    routed = await driver_routing.refresh_routes(now=NOW)

    assert routed == 0
    assert tracker.seen_open_at_http == [0]
    assert tracker.opened_total == 1  # only the read; no persist session
    assert tracker.open == 0


# ── grubops_reconcile._reconcile_branch ───────────────────────────────────────


def _location_ref():
    from app.services.grubops.grubops_reconcile import _LocationRef

    return _LocationRef(
        branch_id=uuid.uuid4(),
        branch_name="Al Majaz",
        grubops_partner_id="partner-1",
        grubops_location_id="loc-1",
    )


def _desired():
    from app.services.grubops.grubops_service import Desired

    return Desired(
        item_map_id=uuid.uuid4(),
        brand_id="brand-1",
        recipe_id="recipe-1",
        modifier_id=None,
        child_modifier_id=None,
        grubops_type="RECIPE",
        available=False,
        until=None,
    )


@pytest.mark.asyncio
async def test_reconcile_branch_pushes_with_no_session_held(monkeypatch):
    from app.services.grubops import grubops_reconcile, grubops_service

    tracker = _Tracker()
    desired = _desired()

    monkeypatch.setattr(grubops_reconcile, "SchedulerSessionFactory", _factory(tracker))

    async def _desired_state(_db, _branch_id):
        return [desired]

    # The read runs on a real (fake) session; `states` come back empty, so a
    # never-pushed mapping is a delta. This exercises phase 1 opening/closing.
    monkeypatch.setattr(grubops_reconcile, "desired_state", _desired_state)

    async def _send_deltas(*, location, deltas):
        tracker.seen_open_at_http.append(tracker.open)
        assert location.grubops_partner_id == "partner-1"
        assert deltas == [desired]

    recorded: dict = {}

    async def _record_pushed(db, *, branch_id, deltas):
        recorded["open"] = tracker.open
        recorded["deltas"] = deltas

    monkeypatch.setattr(grubops_service, "send_deltas", _send_deltas)
    monkeypatch.setattr(grubops_service, "record_pushed", _record_pushed)

    count = await grubops_reconcile._reconcile_branch(_location_ref())

    assert count == 1
    # The GrubOps push ran with no session checked out.
    assert tracker.seen_open_at_http == [0]
    # The read opened and closed before the push.
    assert tracker.events[:2] == ["open", "close"]
    # The record landed on a session reopened after the push, and committed.
    assert recorded["open"] == 1
    assert recorded["deltas"] == [desired]
    assert tracker.events[-1] == "close"
    assert "commit" in tracker.events
    assert tracker.open == 0


@pytest.mark.asyncio
async def test_reconcile_branch_records_failure_on_its_own_session(monkeypatch):
    """A GrubOps failure is recorded on a fresh session, still none held across
    the (failed) call."""
    from app.services.grubops import grubops_reconcile, grubops_service
    from app.services.providers.grubops_provider import GrubOpsError

    tracker = _Tracker()
    desired = _desired()

    monkeypatch.setattr(grubops_reconcile, "SchedulerSessionFactory", _factory(tracker))

    async def _desired_state(_db, _branch_id):
        return [desired]

    monkeypatch.setattr(grubops_reconcile, "desired_state", _desired_state)

    async def _send_deltas(*, location, deltas):
        tracker.seen_open_at_http.append(tracker.open)
        raise GrubOpsError("boom")

    failed: dict = {}

    async def _record_failure(db, *, branch_id, deltas, error):
        failed["open"] = tracker.open
        failed["error"] = error

    monkeypatch.setattr(grubops_service, "send_deltas", _send_deltas)
    monkeypatch.setattr(grubops_service, "record_failure", _record_failure)

    count = await grubops_reconcile._reconcile_branch(_location_ref())

    assert count == 0
    assert tracker.seen_open_at_http == [0]
    assert failed["open"] == 1  # recorded on a reopened session
    assert failed["error"] == "boom"
    assert tracker.open == 0
