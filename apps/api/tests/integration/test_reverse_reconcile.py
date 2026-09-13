"""The reverse reconciliation pass surfaces MM orders the scrape never captured.

An aggregator order that reached MM only through GrubOps (the marketplace's own
push) and was missed by the portal scrape has no `aggregator_order` at all, so the
forward `reconcile_channel` — which walks the aggregator orders — cannot represent
it. `reconcile_reverse_channel` finds those and records an `unmatched_mm` row, and
clears it once the scrape catches up (an aggregator_order links to the order).

Drives the real queries against real rows.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import (
    MATCH_UNMATCHED_MM,
    AggregatorOrder,
    AggregatorReconciliation,
)
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import Warehouse
from app.models.order import Order
from app.models.pos_order import OrderSourceEnum
from app.services.aggregators import reconcile

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-reverse-reconcile"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    """A branch and three talabat MM orders: one scrape-missing (GrubOps-only), one
    already linked to an aggregator_order, one outside the lookback. Yields
    (session_factory, branch_id, {label: (order_id, external_reference)})."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = utcnow()
    refs: dict[str, tuple] = {}
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
        )
        db.add(branch)
        await db.flush()
        branch_id = branch.id
        db.add(Warehouse(branch_id=branch_id, name="Default stock", is_default=True))

        def _order(label, *, created_at):
            ref = f"{MARKER}-{label}-{uuid.uuid4().hex[:8]}"
            order = Order(
                order_number=f"RR-{uuid.uuid4().hex[:12]}",
                email=f"{MARKER}@example.com",
                delivery_method="delivery",
                subtotal=Decimal("40"),
                total=Decimal("40"),
                branch_id=branch_id,
                source=OrderSourceEnum.AGGREGATOR.value,
                aggregator_channel="talabat",
                external_reference=ref,
                created_at=created_at,
            )
            db.add(order)
            refs[label] = order  # resolve id after flush
            return order, ref

        _order("missing", created_at=now - timedelta(days=1))
        linked_order, _ = _order("linked", created_at=now - timedelta(days=1))
        _order("old", created_at=now - timedelta(days=90))
        await db.flush()

        # `linked` HAS an aggregator_order pointing at it → the scrape saw it.
        db.add(
            AggregatorOrder(
                channel="talabat",
                external_order_id=f"AGG-{uuid.uuid4().hex[:10]}",
                business_date="2026-09-12",
                mm_order_id=linked_order.id,
            )
        )
        await db.commit()
        ids = {label: (o.id, o.external_reference) for label, o in refs.items()}

    yield Session, branch_id, ids

    async with Session() as db:
        for oid, _ref in ids.values():
            await db.execute(
                AggregatorOrder.__table__.delete().where(
                    AggregatorOrder.mm_order_id == oid
                )
            )
            await db.execute(
                AggregatorReconciliation.__table__.delete().where(
                    AggregatorReconciliation.mm_order_id == oid
                )
            )
            await db.execute(Order.__table__.delete().where(Order.id == oid))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def _recon_rows(db, ids):
    """The unmatched_mm rows keyed on our three orders' external references."""
    refs = [ref for _oid, ref in ids.values()]
    rows = (
        (
            await db.execute(
                select(AggregatorReconciliation).where(
                    AggregatorReconciliation.channel == "talabat",
                    AggregatorReconciliation.external_order_id.in_(refs),
                )
            )
        )
        .scalars()
        .all()
    )
    return {r.external_order_id: r for r in rows}


async def test_reverse_sweep_flags_only_the_scrape_missed_order(env):
    Session, _branch_id, ids = env
    async with Session() as db:
        n = await reconcile.reconcile_reverse_channel(db, "talabat")
        await db.commit()
        rows = await _recon_rows(db, ids)

    missing_ref = ids["missing"][1]
    linked_ref = ids["linked"][1]
    old_ref = ids["old"][1]

    # Only the GrubOps-only order in the window is surfaced.
    assert missing_ref in rows
    r = rows[missing_ref]
    assert r.match_status == MATCH_UNMATCHED_MM
    assert r.mm_order_id == ids["missing"][0]
    assert r.flags == ["scrape_missing"]
    assert r.total_mm == Decimal("40.00")
    # The linked order (scrape saw it) and the out-of-window order are left alone.
    assert linked_ref not in rows
    assert old_ref not in rows
    assert n >= 1


async def test_reverse_sweep_clears_the_row_once_the_scrape_catches_up(env):
    Session, _branch_id, ids = env
    missing_id, missing_ref = ids["missing"]

    async with Session() as db:
        await reconcile.reconcile_reverse_channel(db, "talabat")
        await db.commit()
        assert missing_ref in await _recon_rows(db, ids)  # surfaced

    # The scrape now ingests the order: an aggregator_order links to it.
    async with Session() as db:
        db.add(
            AggregatorOrder(
                channel="talabat",
                external_order_id=f"AGG-{uuid.uuid4().hex[:10]}",
                business_date="2026-09-12",
                mm_order_id=missing_id,
            )
        )
        await db.commit()

    async with Session() as db:
        await reconcile.reconcile_reverse_channel(db, "talabat")
        await db.commit()
        rows = await _recon_rows(db, ids)

    # The unmatched_mm row is cleared — the order is no longer missing.
    assert missing_ref not in rows
