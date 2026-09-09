"""The sweep that asks noon Send / Slider about bookings gone quiet (F-COU-17).

Both couriers are push-only and never retry, so a dropped terminal push strands
the order. Each already has a `refresh()` that self-heals through `apply_webhook`;
this sweep is what finally calls it automatically. The load-bearing part is the
SELECT — it must pick a stale, non-terminal, push-only booking and leave alone a
terminal one, one still within its quiet window, one a human already owns
(booked beyond CHASE_FOR), and a Lalamove row (reconciled elsewhere). This drives
the real query against real rows with `refresh` stubbed to record who it asked.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import Order
from app.models.order_delivery import (
    NoonSendStatusEnum,
    OrderDelivery,
    SliderStatusEnum,
)
from app.services.couriers import noon_send_service, slider_service
from app.services.delivery import driver_tracking

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-push-reconcile"
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    """A branch and a bag of deliveries, one per case. Yields (session_factory,
    {label: order_id}) and cleans everything up after."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    orders: dict[str, Order] = {}
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        branch_id = branch.id
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        def _delivery(label, **kw):
            order = Order(
                order_number=f"PR-{uuid.uuid4().hex[:12]}",
                email=f"{MARKER}@example.com",
                delivery_method="delivery",
                subtotal=Decimal("10"),
                total=Decimal("10"),
                branch_id=branch.id,
                source="online",
            )
            db.add(order)
            orders[label] = order
            db.add(OrderDelivery(order=order, **kw))

        quiet = NOW - timedelta(minutes=30)
        recent = NOW - timedelta(minutes=2)
        # Eligible: stale, non-terminal, push-only.
        _delivery(
            "noon_stale",
            provider="noon_send",
            courier_order_id="N-stale",
            courier_status=NoonSendStatusEnum.ASSIGNED.value,
            status_updated_at=quiet,
            booked_at=NOW - timedelta(hours=1),
        )
        # Eligible: a booking that never got even its first push (NULL status).
        # Stored as `slider_car`, the tier dispatch actually records — the sweep
        # must resolve that to slider, not miss it by keying on the bare name
        # (the MM-20260909-001 provider-family bug).
        _delivery(
            "slider_nullstatus",
            provider="slider_car",
            courier_order_id="S-null",
            courier_status=None,
            status_updated_at=None,
            booked_at=quiet,
        )
        # Skip: already terminal.
        _delivery(
            "noon_terminal",
            provider="noon_send",
            courier_order_id="N-done",
            courier_status=NoonSendStatusEnum.DELIVERED.value,
            status_updated_at=quiet,
            booked_at=NOW - timedelta(hours=1),
        )
        # Skip: still inside its quiet window.
        _delivery(
            "noon_recent",
            provider="noon_send",
            courier_order_id="N-recent",
            courier_status=NoonSendStatusEnum.ASSIGNED.value,
            status_updated_at=recent,
            booked_at=recent,
        )
        # Skip: booked beyond CHASE_FOR, a human owns it now. A slider_bike tier,
        # so both Slider tiers are exercised.
        _delivery(
            "slider_ancient",
            provider="slider_bike",
            courier_order_id="S-old",
            courier_status=SliderStatusEnum.HEADING_TO_PICKUP.value,
            status_updated_at=quiet,
            booked_at=NOW - timedelta(hours=8),
        )
        # Skip: Lalamove is reconciled by refresh_live_drivers, not here.
        _delivery(
            "lalamove",
            provider="lalamove",
            courier_order_id="L-1",
            courier_status="ON_GOING",
            status_updated_at=quiet,
            booked_at=NOW - timedelta(hours=1),
        )
        await db.commit()
        order_ids = {label: order.id for label, order in orders.items()}

    yield Session, order_ids

    async with Session() as db:
        for oid in order_ids.values():
            await db.execute(
                OrderDelivery.__table__.delete().where(OrderDelivery.order_id == oid)
            )
            await db.execute(Order.__table__.delete().where(Order.id == oid))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_it_asks_only_the_stale_non_terminal_push_only_bookings(
    engine, env, monkeypatch
):
    Session, ids = env
    asked: list[uuid.UUID] = []

    async def record(_db, order_id):
        asked.append(order_id)

    monkeypatch.setattr(noon_send_service, "is_enabled", lambda: True)
    monkeypatch.setattr(slider_service, "is_enabled", lambda: True)
    monkeypatch.setattr(noon_send_service, "refresh", record)
    monkeypatch.setattr(slider_service, "refresh", record)

    # A high limit so this test's rows are not crowded out of the page by other
    # data in the shared database; membership, not equality, for the same reason.
    async with Session() as db:
        await driver_tracking.reconcile_push_only_endings(db, limit=10_000, now=NOW)

    asked_set = set(asked)
    # The two eligible bookings were asked about.
    assert ids["noon_stale"] in asked_set
    assert ids["slider_nullstatus"] in asked_set
    # The four ineligible ones were left alone.
    assert ids["noon_terminal"] not in asked_set  # already finished
    assert ids["noon_recent"] not in asked_set  # still inside its quiet window
    assert ids["slider_ancient"] not in asked_set  # a human owns it now
    assert ids["lalamove"] not in asked_set  # reconciled by the driver sweep


async def test_it_does_nothing_when_neither_courier_is_configured(
    engine, env, monkeypatch
):
    Session, _ = env
    monkeypatch.setattr(noon_send_service, "is_enabled", lambda: False)
    monkeypatch.setattr(slider_service, "is_enabled", lambda: False)

    async with Session() as db:
        assert await driver_tracking.reconcile_push_only_endings(db, now=NOW) == 0
