"""Busy mode against a real Postgres: the switch, the website, the marketplaces.

What a mocked session cannot show:

- the switch is idempotent and leaves a trail, and queues one row per channel
  the branch actually trades on;
- every query that routes a website order to a branch walks around a busy one
  (and a pickup at a busy shop is refused by name, not quietly re-routed);
- the reconciler closes and reopens each outlet through the hours writers,
  re-closes after midnight, backs off on failure, and never marks a channel
  done for a toggle that was reversed while its portal call was in flight;
- Keeta goes through the worker command queue and the chip follows the
  worker's report.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.aggregator import AggregatorBranchMap, BranchHoursSyncRun
from app.models.base import utcnow
from app.models.branch import Branch, BranchWeeklyHours
from app.models.busy_mode import (
    AggregatorWorkerCommand,
    BranchBusyEvent,
    BranchChannelBusyState,
)
from app.models.inventory import Warehouse
from app.services import push_service
from app.services.aggregators import busy_sync, hours_writers, worker_commands
from app.services.catalog import availability_service, catalogue_cache
from app.services.delivery import fulfilment_service
from app.services.orders import order_service
from app.services.pos import busy_mode_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-busy"


@pytest.fixture
async def Session(monkeypatch):
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The reconciler opens its own sessions; point them at the test database.
    monkeypatch.setattr(busy_sync, "SchedulerSessionFactory", factory)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No Redis, no APNs: record the calls instead."""
    calls: dict[str, list] = {"retire": [], "push": []}

    async def retire():
        calls["retire"].append(True)

    async def push(db, **kwargs):
        calls["push"].append(kwargs)
        return 0

    monkeypatch.setattr(catalogue_cache, "retire", retire)
    monkeypatch.setattr(push_service, "notify_branch_busy", push)
    return calls


@pytest.fixture
def live_writes(monkeypatch):
    monkeypatch.setattr(settings, "CATALOG_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "BRANCH_HOURS_SYNC_LIVE", True)


async def _branch(
    Session, *, channels=("talabat", "deliveroo", "keeta"), pickup=False, hours=True
) -> uuid.UUID:
    async with Session() as db:
        tag = uuid.uuid4().hex[:10]
        branch = Branch(
            name=f"{MARKER} {tag}",
            reference=f"BZ{tag}",
            offers_pickup=pickup,
            latitude=25.1 if pickup else None,
            longitude=55.2 if pickup else None,
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
        for channel in channels:
            db.add(
                AggregatorBranchMap(
                    channel=channel,
                    branch_id=branch.id,
                    external_outlet_id=f"{channel}-{tag}",
                    is_active=True,
                )
            )
        if hours:
            for weekday in range(7):
                db.add(
                    BranchWeeklyHours(
                        branch_id=branch.id,
                        weekday=weekday,
                        shift_index=0,
                        opens="08:00",
                        closes="22:00",
                    )
                )
        await db.commit()
        return branch.id


async def _load(db, branch_id) -> Branch:
    branch = await db.get(Branch, branch_id)
    assert branch is not None
    return branch


async def _rows(Session, branch_id) -> dict[str, BranchChannelBusyState]:
    async with Session() as db:
        rows = (
            (
                await db.execute(
                    select(BranchChannelBusyState).where(
                        BranchChannelBusyState.branch_id == branch_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {r.channel: r for r in rows}


async def _toggle(Session, branch_id, busy: bool):
    async with Session() as db:
        state = await busy_mode_service.set_busy(
            db,
            await _load(db, branch_id),
            busy,
            actor_id=None,
            device_id=None,
            source="admin",
        )
        await db.commit()
        return state


# ─── The switch ───────────────────────────────────────────────────────────────


async def test_switching_on_queues_every_channel_once_and_leaves_a_trail(
    Session, quiet
):
    branch_id = await _branch(Session)
    state = await _toggle(Session, branch_id, True)
    assert state.busy and state.since is not None
    assert {c.channel: c.state for c in state.channels} == {
        "deliveroo": "closing",
        "keeta": "closing",
        "talabat": "closing",
    }
    assert quiet["retire"] and quiet["push"][0]["busy"] is True

    # A second "on" from another till is a no-op: nothing re-queued, no event.
    await _toggle(Session, branch_id, True)
    async with Session() as db:
        events = (
            (
                await db.execute(
                    select(BranchBusyEvent).where(
                        BranchBusyEvent.branch_id == branch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1 and events[0].busy is True

    state = await _toggle(Session, branch_id, False)
    assert not state.busy and state.since is None
    rows = await _rows(Session, branch_id)
    assert {r.desired for r in rows.values()} == {"open"}
    assert {r.status for r in rows.values()} == {"pending"}


# ─── The website ──────────────────────────────────────────────────────────────


async def test_the_website_routes_around_a_busy_branch(Session):
    branch_id = await _branch(Session, channels=(), pickup=True)
    async with Session() as db:
        assert await order_service._load_active_branch(db, branch_id) is not None
        assert branch_id in {b.id for b in await fulfilment_service.pickup_branches(db)}

    await _toggle(Session, branch_id, True)
    async with Session() as db:
        assert await order_service._load_active_branch(db, branch_id) is None
        assert branch_id not in {
            b.id for b in await fulfilment_service.pickup_branches(db)
        }
        ids = (
            (await db.execute(availability_service._website_delivery_branch_ids()))
            .scalars()
            .all()
        )
        assert branch_id not in ids
        any_branch = await order_service.resolve_branch(db, None)
        assert any_branch is None or any_branch.id != branch_id


async def test_a_pickup_at_a_busy_shop_is_refused_by_name(Session):
    branch_id = await _branch(Session, channels=(), pickup=True)
    await _toggle(Session, branch_id, True)
    async with Session() as db:
        choice = await order_service.select_fulfilment(
            db, None, None, pickup_branch_id=branch_id
        )
    assert isinstance(choice, order_service.NoBranchFulfils)
    assert choice.busy_branch is not None and choice.busy_branch.id == branch_id


# ─── The reconciler ───────────────────────────────────────────────────────────


@pytest.fixture
def writers(monkeypatch):
    """Stand-ins for the portal writes; each records what it was asked."""
    calls: list[tuple[str, str]] = []
    fail: set[str] = set()

    async def close_outlet(db, *, channel, branch, dry_run=True):
        if channel in fail:
            raise RuntimeError(f"{channel} returned 401")
        calls.append(("close", channel))
        return {"endpoint": f"close {channel}"}

    async def push_weekly_hours(db, *, channel, branch, weekly, dry_run=True):
        calls.append(("weekly", channel))
        return {"endpoint": f"weekly {channel}"}

    monkeypatch.setattr(hours_writers, "close_outlet", close_outlet)
    monkeypatch.setattr(hours_writers, "push_weekly_hours", push_weekly_hours)
    return calls, fail


async def _drain(Session, branch_id):
    async with Session() as db:
        due = [s for s in await busy_sync._due(db) if s.branch_id == branch_id]
    for seen in due:
        await busy_sync.apply_one(seen)
    return due


async def test_close_then_reopen_each_outlet_its_own_way(Session, writers, live_writes):
    calls, _ = writers
    branch_id = await _branch(Session)
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)

    assert sorted(calls) == [("close", "deliveroo"), ("close", "talabat")]
    rows = await _rows(Session, branch_id)
    assert rows["talabat"].status == "done"
    assert rows["talabat"].applied_for_date == busy_sync._today()
    # Keeta waits on the worker: still pending, a command queued.
    assert rows["keeta"].status == "pending"
    async with Session() as db:
        command = (
            await db.execute(
                select(AggregatorWorkerCommand).where(
                    AggregatorWorkerCommand.branch_id == branch_id
                )
            )
        ).scalar_one()
        assert command.desired == "closed" and command.status == "pending"
        # While busy, the hourly mirror leaves these outlets alone.
        assert await busy_sync.owns_channel(db, branch_id, "deliveroo")

    calls.clear()
    await _toggle(Session, branch_id, False)
    await _drain(Session, branch_id)
    # Both get the whole week back from MM's schedule.
    assert sorted(calls) == [("weekly", "deliveroo"), ("weekly", "talabat")]
    async with Session() as db:
        assert not await busy_sync.owns_channel(db, branch_id, "deliveroo")
        runs = (
            (
                await db.execute(
                    select(BranchHoursSyncRun).where(
                        BranchHoursSyncRun.branch_id == branch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {r.planned["op"] for r in runs} == {"busy_close", "busy_open"}


async def test_a_branch_still_busy_after_midnight_is_closed_again(
    Session, writers, live_writes
):
    calls, _ = writers
    branch_id = await _branch(Session, channels=("talabat",))
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)
    assert calls == [("close", "talabat")]

    # Nothing due while today's close holds…
    assert await _drain(Session, branch_id) == []
    # …but yesterday's lapses at midnight on the portal side.
    async with Session() as db:
        await db.execute(
            update(BranchChannelBusyState)
            .where(BranchChannelBusyState.branch_id == branch_id)
            .values(applied_for_date=busy_sync._today() - timedelta(days=1))
        )
        await db.commit()
    await _drain(Session, branch_id)
    assert calls == [("close", "talabat"), ("close", "talabat")]


async def test_a_failed_write_backs_off_and_reports_why(Session, writers, live_writes):
    calls, fail = writers
    fail.add("deliveroo")
    branch_id = await _branch(Session, channels=("deliveroo",))
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)

    row = (await _rows(Session, branch_id))["deliveroo"]
    assert row.status == "failed" and row.attempts == 1
    assert "401" in (row.last_error or "")
    assert row.next_attempt_at is not None and row.next_attempt_at > utcnow()
    # Not due again until the backoff elapses.
    assert await _drain(Session, branch_id) == []
    async with Session() as db:
        chips = (
            await busy_mode_service.status(db, await _load(db, branch_id))
        ).channels
    assert chips[0].state == "failed" and "401" in (chips[0].error or "")


async def test_a_toggle_reversed_mid_write_is_not_marked_done(
    Session, writers, live_writes
):
    branch_id = await _branch(Session, channels=("talabat",))
    await _toggle(Session, branch_id, True)
    async with Session() as db:
        (seen,) = [s for s in await busy_sync._due(db) if s.branch_id == branch_id]
    # Staff turn it off while the close is in flight.
    await _toggle(Session, branch_id, False)
    await busy_sync.apply_one(seen)

    row = (await _rows(Session, branch_id))["talabat"]
    assert row.desired == "open" and row.status == "pending"


async def test_without_a_schedule_an_hours_close_is_refused_not_made(
    Session, writers, live_writes
):
    calls, _ = writers
    branch_id = await _branch(Session, channels=("deliveroo",), hours=False)
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)
    assert calls == []
    row = (await _rows(Session, branch_id))["deliveroo"]
    assert row.status == "unsupported"


async def test_with_writes_switched_off_every_channel_reads_manual(Session):
    branch_id = await _branch(Session, channels=("talabat", "keeta"))
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)
    async with Session() as db:
        chips = (
            await busy_mode_service.status(db, await _load(db, branch_id))
        ).channels
    assert {c.state for c in chips} == {"manual"}


# ─── Keeta, through the worker ────────────────────────────────────────────────


async def test_keeta_chip_follows_the_worker(Session, writers, live_writes):
    branch_id = await _branch(Session, channels=("keeta",))
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)

    async with Session() as db:
        claimed = await worker_commands.claim(db, channel="keeta")
        await db.commit()
    mine = [c for c in claimed if c.branch_id == branch_id]
    assert len(mine) == 1 and mine[0].desired == "closed"

    # Claimed commands are not handed out twice.
    async with Session() as db:
        again = await worker_commands.claim(db, channel="keeta")
        await db.commit()
    assert not [c for c in again if c.branch_id == branch_id]

    async with Session() as db:
        await worker_commands.complete(
            db, mine[0].id, ok=True, error=None, result={"businessStatus": 0}
        )
        await db.commit()
    row = (await _rows(Session, branch_id))["keeta"]
    assert row.status == "done" and row.applied_for_date == busy_sync._today()


async def test_the_latest_keeta_request_wins(Session, writers, live_writes):
    branch_id = await _branch(Session, channels=("keeta",))
    async with Session() as db:
        for desired in ("closed", "open", "closed"):
            await worker_commands.queue(
                db,
                channel="keeta",
                kind=worker_commands.KEETA_SHOP_STATUS,
                branch_id=branch_id,
                external_shop_id="shop",
                desired=desired,
            )
        await db.commit()
        pending = (
            (
                await db.execute(
                    select(AggregatorWorkerCommand).where(
                        AggregatorWorkerCommand.branch_id == branch_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 1 and pending[0].desired == "closed"


async def test_a_worker_report_for_a_reversed_toggle_does_not_move_the_chip(
    Session, writers, live_writes
):
    branch_id = await _branch(Session, channels=("keeta",))
    await _toggle(Session, branch_id, True)
    await _drain(Session, branch_id)
    async with Session() as db:
        (command,) = [
            c
            for c in await worker_commands.claim(db, channel="keeta")
            if c.branch_id == branch_id
        ]
        await db.commit()
    await _toggle(Session, branch_id, False)
    async with Session() as db:
        await worker_commands.complete(db, command.id, ok=True, error=None, result=None)
        await db.commit()
    row = (await _rows(Session, branch_id))["keeta"]
    assert row.desired == "open" and row.status == "pending"
