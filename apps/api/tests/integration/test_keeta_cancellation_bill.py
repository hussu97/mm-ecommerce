"""A Keeta cancellation's provisional net, overridden once the shop's bill lands.

`promote._keeta_billed_without` answers "has this shop's weekly bill covering the
order's day arrived without the order on it?", and the statement ingest nudges
exactly those cancelled orders so promote re-books them at 0.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import AggregatorOrder, AggregatorStatement
from app.services.aggregators import ingest, promote

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

SHOP = "9990001"


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _cancelled(day: str, *, shop: str = SHOP, statement_id=None) -> AggregatorOrder:
    return AggregatorOrder(
        channel="keeta",
        external_order_id=f"pytest-keeta-{uuid.uuid4().hex[:10]}",
        business_date=day,
        status="cancelled",
        cancelled_at=datetime(2031, 3, 5, 12, tzinfo=timezone.utc),
        net_payable=Decimal("26.40"),
        statement_id=statement_id,
        raw={"shopId": int(shop), "canceledScene": 5050},
    )


async def test_billed_without_needs_the_shops_bill_for_that_week(db):
    in_week = _cancelled("2031-03-05")
    other_shop = _cancelled("2031-03-05", shop="9990002")
    next_week = _cancelled("2031-03-12")
    on_the_bill = _cancelled("2031-03-05", statement_id="KEETA_BILL_X")
    db.add_all([in_week, other_shop, next_week, on_the_bill])
    db.add(
        AggregatorStatement(
            channel="keeta",
            statement_id=f"KEETA_BILL_{SHOP}_2031-03-03_2031-03-09",
            external_outlet_id=SHOP,
            period_start="2031-03-03",
            period_end="2031-03-09",
        )
    )
    await db.flush()

    assert await promote._keeta_billed_without(db, in_week) is True
    assert await promote._keeta_billed_without(db, other_shop) is False
    assert await promote._keeta_billed_without(db, next_week) is False
    assert await promote._keeta_billed_without(db, on_the_bill) is False


async def test_a_bill_nudges_only_its_shops_unsettled_cancellations(db):
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
    left_off = _cancelled("2031-03-05")
    settled = _cancelled("2031-03-05", statement_id="KEETA_BILL_X")
    other_shop = _cancelled("2031-03-05", shop="9990002")
    db.add_all([left_off, settled, other_shop])
    await db.flush()
    for row in (left_off, settled, other_shop):
        row.updated_at = stale
    await db.flush()

    statement = SimpleNamespace(
        external_outlet_id=SHOP, period_start="2031-03-03", period_end="2031-03-09"
    )
    touched = await ingest._touch_keeta_cancellations_left_off(db, "keeta", statement)
    assert touched == 1

    rows = {
        r.external_order_id: r.updated_at
        for r in (
            await db.execute(
                select(AggregatorOrder)
                .where(
                    AggregatorOrder.external_order_id.in_(
                        [
                            left_off.external_order_id,
                            settled.external_order_id,
                            other_shop.external_order_id,
                        ]
                    )
                )
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert rows[left_off.external_order_id] > stale + timedelta(days=1)
    assert rows[settled.external_order_id] == stale
    assert rows[other_shop.external_order_id] == stale
