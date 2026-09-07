"""
Sales-by-modifier-option must respect each option's own quantity.

F-POS-20: a mixed box records a per-option `quantity` in the line's option
snapshot (two oat-milk, four full-fat in a box of six), but the report counted
each option once per line and so under-reported the mix. The fix multiplies by
`coalesce((option->>'quantity')::numeric, 1)`. Verified against a real Postgres
because the bug is in what the aggregate multiplies.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderItem, OrderStatusEnum
from app.services.pos.pos_reports import sales_by_dimension

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-modifier-option"
BDATE = "2026-09-04"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
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

        order = Order(
            order_number=f"MO-{uuid.uuid4().hex[:14]}",
            email="pytest-mod@example.com",
            source="cashier",
            branch_id=branch.id,
            is_pos=True,
            business_date=BDATE,
            status=OrderStatusEnum.DELIVERED.value,
            pos_status="closed",
            closed_at=datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc),
            delivery_method=DeliveryMethodEnum.PICKUP,
            subtotal=Decimal("60.00"),
            total=Decimal("60.00"),
        )
        db.add(order)
        await db.flush()
        # A box bought 3 times. Each box: 2 oat-milk (2.00 each) + 1 sugar (no
        # quantity field → defaults to 1).
        db.add(
            OrderItem(
                order_id=order.id,
                product_name="Mixed box",
                product_sku=f"SKU-{uuid.uuid4().hex[:8]}",
                quantity=3,
                base_price=Decimal("20.00"),
                unit_price=Decimal("20.00"),
                total_price=Decimal("60.00"),
                returned_quantity=0,
                status="active",
                selected_options_snapshot=[
                    {"name": "Oat milk", "price": "2.00", "quantity": 2},
                    {"name": "Sugar", "price": "0.00"},
                ],
            )
        )
        await db.commit()
        branch_id = branch.id

    yield branch_id

    async with Session() as db:
        rows = (
            await db.execute(
                Order.__table__.select().where(Order.branch_id == branch_id)
            )
        ).all()
        order_ids = [r.id for r in rows]
        if order_ids:
            await db.execute(
                OrderItem.__table__.delete().where(OrderItem.order_id.in_(order_ids))
            )
        await db.execute(Order.__table__.delete().where(Order.branch_id == branch_id))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_option_quantity_is_multiplied(seeded, engine):
    branch_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        rows = await sales_by_dimension(
            db,
            dimension="modifier_option",
            branch_id=branch_id,
            date_from=BDATE,
            date_to=BDATE,
        )
    by_name = {r["label"]: r for r in rows}

    # Oat milk: 2 per box × 3 boxes = 6 units, 2.00 each = 12.00.
    assert by_name["Oat milk"]["quantity"] == 6
    assert by_name["Oat milk"]["net_sales"] == Decimal("12.00")
    # Sugar has no quantity field → defaults to 1 per box × 3 boxes = 3.
    assert by_name["Sugar"]["quantity"] == 3
