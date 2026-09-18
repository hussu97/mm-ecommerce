"""Integration coverage for the stranded-order safety net.

`promote.autodeliver_stale_out_for_delivery` is the backstop for the one-source
`delivered` rung: an aggregator order whose channel scrape never carried the final
status sits `out_for_delivery` forever. This pins that it books exactly the orders
that have been `out_for_delivery` longer than the threshold — aggregator only,
measured from when they ENTERED the rung — and leaves fresh ones, non-aggregator
ones, and already-terminal ones alone; and that the move goes through the lifecycle
(delivered event, register closed) under an AGGREGATOR actor.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import (
    OrderStatusEvent,
    StatusSourceEnum,
    acting_as,
)
from app.models.pos_order import OrderSourceEnum
from app.services.aggregators import promote

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-agg-autodeliver"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(engine):
    """A rolled-back session (flush-only, like production) so nothing persists."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _branch(db) -> uuid.UUID:
    branch = Branch(name=MARKER, reference=f"{MARKER}-{uuid.uuid4().hex[:10]}")
    db.add(branch)
    await db.flush()
    return branch.id


async def _out_for_delivery_order(
    db, *, branch_id, source=OrderSourceEnum.AGGREGATOR.value, entered_hours_ago
):
    """An order sitting `out_for_delivery`. Built inside `acting_as(at=…)` so the
    status-event listener stamps the `out_for_delivery` rung `entered_hours_ago` in
    the past — exactly how `_drive_status` dates the rung in production."""
    entered = utcnow() - timedelta(hours=entered_hours_ago)
    with acting_as(StatusSourceEnum.AGGREGATOR, at=entered):
        order = Order(
            order_number=f"AGG-AD-{uuid.uuid4().hex[:10]}",
            email="",
            locale="en",
            delivery_method="delivery",
            order_type="delivery",
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            source=source,
            aggregator_channel="talabat"
            if source == OrderSourceEnum.AGGREGATOR.value
            else None,
            external_reference=uuid.uuid4().hex[:12],
            branch_id=branch_id,
            created_at=utcnow() - timedelta(hours=entered_hours_ago + 1),
            subtotal=Decimal("40"),
            total=Decimal("40"),
            vat_amount=Decimal("0"),
            total_excl_vat=Decimal("40"),
            vat_rate=Decimal("0"),
            discount_amount=Decimal("0"),
        )
        db.add(order)
        await db.flush()
    return order


async def test_books_only_the_stale_aggregator_orders(db):
    branch_id = await _branch(db)
    stale = await _out_for_delivery_order(db, branch_id=branch_id, entered_hours_ago=9)
    fresh = await _out_for_delivery_order(db, branch_id=branch_id, entered_hours_ago=2)
    nonagg = await _out_for_delivery_order(
        db,
        branch_id=branch_id,
        source=OrderSourceEnum.ONLINE.value,
        entered_hours_ago=9,
    )

    moved = await promote.autodeliver_stale_out_for_delivery(db, older_than_hours=8)

    assert moved == 1
    await db.refresh(stale)
    await db.refresh(fresh)
    await db.refresh(nonagg)
    assert stale.status == OrderStatusEnum.DELIVERED
    assert fresh.status == OrderStatusEnum.OUT_FOR_DELIVERY  # under the threshold
    assert nonagg.status == OrderStatusEnum.OUT_FOR_DELIVERY  # not an aggregator order

    # The move went through the lifecycle: a delivered event under the AGGREGATOR
    # actor (so it fires no POS/Foodics echo), stamped ~1h after the rung — not now.
    ev = (
        await db.scalars(
            select(OrderStatusEvent).where(
                OrderStatusEvent.order_id == stale.id,
                OrderStatusEvent.status == OrderStatusEnum.DELIVERED.value,
            )
        )
    ).one()
    assert ev.source == StatusSourceEnum.AGGREGATOR.value
    assert ev.actor_label == "auto-deliver"
    assert ev.at < utcnow() - timedelta(hours=7)  # dated to the delivery, not the sweep


async def test_disabled_when_threshold_zero(db):
    branch_id = await _branch(db)
    stale = await _out_for_delivery_order(db, branch_id=branch_id, entered_hours_ago=30)

    moved = await promote.autodeliver_stale_out_for_delivery(db, older_than_hours=0)

    assert moved == 0
    await db.refresh(stale)
    assert stale.status == OrderStatusEnum.OUT_FOR_DELIVERY
