"""
The sales-summary funnel must reconcile, and returns must not be counted twice.

F-POS-16: `Order.subtotal` is stored POST-return (the pricing engine sums the
gross of billable units), yet the summary read it as gross AND subtracted a
returns line computed from `returned_quantity * unit_price` — removing the
returned units twice. The fix states ONE convention: `gross_sales` is PRE-return
(`subtotal + returns`), and the funnel identity

    net_sales == gross_sales − returns − discounts + charges + rounding

holds exactly. This seeds orders — including one with a partially returned line —
against a real Postgres and asserts that identity and the returned value.
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
from app.services.pos.pos_reports import sales_summary

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-summary-reconcile"
BDATE = "2026-09-03"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _closed_order(
    branch_id, *, subtotal, discount, charges, rounding, total, vat="0.00"
):
    """A closed counter order whose stored aggregates satisfy the pricing
    identity total == subtotal − discount + charges + rounding."""
    return Order(
        order_number=f"SR-{uuid.uuid4().hex[:14]}",
        email="pytest-reconcile@example.com",
        source="cashier",
        branch_id=branch_id,
        is_pos=True,
        business_date=BDATE,
        status=OrderStatusEnum.DELIVERED.value,
        pos_status="closed",
        closed_at=datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc),
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal(str(subtotal)),
        discount_amount=Decimal(str(discount)),
        charges_amount=Decimal(str(charges)),
        rounding_amount=Decimal(str(rounding)),
        vat_amount=Decimal(str(vat)),
        total=Decimal(str(total)),
    )


def _item(order_id, *, qty, unit_price, returned, total_price):
    return OrderItem(
        order_id=order_id,
        product_name="Brownie",
        product_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        quantity=qty,
        base_price=Decimal(str(unit_price)),
        options_price=Decimal("0.00"),
        unit_price=Decimal(str(unit_price)),
        total_price=Decimal(str(total_price)),
        returned_quantity=returned,
        status="active",
    )


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

        # Order 1: 5 @ 10.00, 2 returned → billable 3, subtotal 30.00, no
        # discount/charges/tax. Returns retail = 2 × 10 = 20.00. total = 30.
        o1 = _closed_order(
            branch.id,
            subtotal="30.00",
            discount="0.00",
            charges="0.00",
            rounding="0.00",
            total="30.00",
        )
        # Order 2: 1 @ 100.00, none returned, 10 off, 5 service charge.
        # total = 100 − 10 + 5 + 0 = 95.00.
        o2 = _closed_order(
            branch.id,
            subtotal="100.00",
            discount="10.00",
            charges="5.00",
            rounding="0.00",
            total="95.00",
        )
        db.add_all([o1, o2])
        await db.flush()
        db.add(_item(o1.id, qty=5, unit_price="10.00", returned=2, total_price="30.00"))
        db.add(
            _item(o2.id, qty=1, unit_price="100.00", returned=0, total_price="90.00")
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


async def test_the_funnel_reconciles_and_returns_are_counted_once(seeded, engine):
    branch_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        s = await sales_summary(db, branch_id=branch_id, date_from=BDATE, date_to=BDATE)

    # Returns are the retail value of the two returned units: 2 × 10.00.
    assert s["returns"] == Decimal("20.00")
    # gross_sales is PRE-return: 30 + 100 (post-return) + 20 (returns) = 150.
    assert s["gross_sales"] == Decimal("150.00")
    assert s["discounts"] == Decimal("10.00")
    assert s["charges"] == Decimal("5.00")
    assert s["net_sales"] == Decimal("125.00")

    # THE funnel identity — the whole point of the finding.
    funnel = (
        s["gross_sales"] - s["returns"] - s["discounts"] + s["charges"] + s["rounding"]
    )
    assert funnel == s["net_sales"], "the sales funnel does not reconcile"

    # And the returned units are gone from net exactly once: had returns been
    # double-subtracted, net would read 125 − 20 = 105 somewhere in the chain.
    assert (
        s["net_sales"]
        == s["gross_sales"]
        - s["returns"]
        - s["discounts"]
        + s["charges"]
        + s["rounding"]
    )
