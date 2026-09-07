"""
The sales forecast must only average the lookback window, not all of history.

F-POS-17: `sales_predictions` averaged every closed order ever — `lookback_weeks`
reached nothing but the confidence label — and seq-scanned `orders` per request.
The fix bounds the query on `business_date >= today − lookback_weeks` (and rides
the partial index migration 205 adds). This seeds same-weekday history inside and
outside the window and asserts the old rows outside it are ignored.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.pos import business_day_service
from app.services.pos.pos_reports import sales_predictions

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-forecast-window"
LOOKBACK_WEEKS = 8


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    today = business_day_service.shop_today()
    # Same weekday as `today + 7` (a date inside the 7-day forecast horizon), so
    # the prediction for that weekday reads this seeded history.
    recent = [today - timedelta(days=7 * k) for k in (1, 2, 3)]  # within 8 weeks
    old = today - timedelta(days=70)  # 70 > 56 days → outside the window
    weekday_name = (today).strftime("%A")

    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch",
            reference=f"{MARKER}-{uuid.uuid4().hex[:8]}",
            business_day_start="04:00",
            is_active=True,
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        def closed(bdate, total):
            return Order(
                order_number=f"FC-{uuid.uuid4().hex[:14]}",
                email="pytest-forecast@example.com",
                source="cashier",
                branch_id=branch.id,
                is_pos=True,
                business_date=bdate.isoformat(),
                status=OrderStatusEnum.DELIVERED.value,
                pos_status="closed",
                closed_at=datetime(
                    bdate.year, bdate.month, bdate.day, 20, 0, tzinfo=timezone.utc
                ),
                delivery_method=DeliveryMethodEnum.PICKUP,
                subtotal=Decimal(str(total)),
                total=Decimal(str(total)),
            )

        for d in recent:
            db.add(closed(d, "100.00"))
        db.add(closed(old, "9999.00"))  # must NOT drag the average
        await db.commit()
        branch_id = branch.id

    yield branch_id, weekday_name

    async with Session() as db:
        await db.execute(Order.__table__.delete().where(Order.branch_id == branch_id))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_forecast_ignores_history_older_than_the_lookback(seeded, engine):
    branch_id, weekday_name = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        predictions = await sales_predictions(
            db, branch_id=branch_id, days_ahead=7, lookback_weeks=LOOKBACK_WEEKS
        )

    row = next(p for p in predictions if p["weekday"] == weekday_name)
    # Three recent same-weekday days at 100 each → 100 average. The 9999 old day
    # is outside the window and does not count.
    assert row["based_on_days"] == 3
    assert row["predicted_sales"] == Decimal("100.00")
