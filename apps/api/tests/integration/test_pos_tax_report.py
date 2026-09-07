"""
The VAT report must call a sale what the sales summary calls a sale.

F-POS-8: `tax_report` scoped on `pos_status == closed`, so a delivered website
order's VAT reached the sales summary (which uses `_COMPLETED_SALE`) but never
the tax report — the two disagreed about which orders are sales and their VAT
lines could not reconcile. The fix scopes the tax report on the same
`_COMPLETED_SALE` predicate, and adds a `discrepancies` count: completed-sale
orders whose stamped `vat_amount` does not equal the sum of their own tax lines.

Against a real Postgres because the bug is which rows the WHERE clause keeps.
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
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.pos_order import OrderTax
from app.services.pos.pos_reports import tax_report

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-tax-report"
BDATE = "2026-09-02"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(branch_id, *, source, status, vat, pos_status=None):
    closed_at = (
        datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
        if pos_status == "closed"
        else None
    )
    return Order(
        order_number=f"TX-{uuid.uuid4().hex[:14]}",
        email="pytest-tax@example.com",
        source=source,
        branch_id=branch_id,
        is_pos=True,
        business_date=BDATE,
        status=status,
        pos_status=pos_status,
        closed_at=closed_at,
        delivery_method=DeliveryMethodEnum.DELIVERY,
        subtotal=Decimal("100.00"),
        total=Decimal("100.00"),
        vat_amount=Decimal(str(vat)),
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

        S = OrderStatusEnum

        def tax(order, amount):
            return OrderTax(
                order_id=order.id,
                name="VAT",
                rate=Decimal("0.0500"),
                taxable_amount=Decimal("100.00"),
                amount=Decimal(str(amount)),
            )

        # A: website delivered, VAT reconciles → counted, no discrepancy.
        a = _order(branch.id, source="online", status=S.DELIVERED.value, vat="5.00")
        # B: counter closed, VAT reconciles → counted, no discrepancy.
        b = _order(
            branch.id,
            source="cashier",
            status=S.DELIVERED.value,
            pos_status="closed",
            vat="2.50",
        )
        # C: website NOT delivered → excluded from both the rates and discrepancies.
        c = _order(branch.id, source="online", status=S.CONFIRMED.value, vat="99.00")
        # D: website delivered but its tax lines do NOT sum to vat_amount → a
        # discrepancy, still counted in the rate bucket at its line value (7.00).
        d = _order(branch.id, source="online", status=S.DELIVERED.value, vat="10.00")
        for o in (a, b, c, d):
            db.add(o)
        await db.flush()
        db.add_all([tax(a, "5.00"), tax(b, "2.50"), tax(c, "99.00"), tax(d, "7.00")])
        await db.commit()
        branch_id = branch.id

    yield branch_id

    async with Session() as db:
        ids = (
            await db.execute(
                Order.__table__.select().where(Order.branch_id == branch_id)
            )
        ).all()
        order_ids = [r.id for r in ids]
        if order_ids:
            await db.execute(
                OrderTax.__table__.delete().where(OrderTax.order_id.in_(order_ids))
            )
        await db.execute(Order.__table__.delete().where(Order.branch_id == branch_id))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_tax_report_scopes_on_completed_sale_not_closed_alone(seeded, engine):
    branch_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        result = await tax_report(
            db, branch_id=branch_id, date_from=BDATE, date_to=BDATE
        )

    rates = result["rates"]
    assert len(rates) == 1, "one VAT rate bucket expected"
    # 5.00 (delivered website) + 2.50 (closed counter) + 7.00 (delivered website D)
    # — but NOT the 99.00 on the un-delivered order C, which is not a sale.
    assert rates[0]["tax_amount"] == Decimal("14.50")
    assert rates[0]["rate_percent"] == 5.0


async def test_discrepancies_count_flags_only_mismatched_completed_sales(
    seeded, engine
):
    branch_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        result = await tax_report(
            db, branch_id=branch_id, date_from=BDATE, date_to=BDATE
        )

    # Only order D (vat_amount 10.00 vs tax lines 7.00) is a completed sale that
    # does not reconcile. A and B reconcile; C is not a completed sale at all.
    assert result["discrepancies"] == 1
