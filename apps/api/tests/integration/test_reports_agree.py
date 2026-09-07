"""
The owner's inbox and the manager console must agree on "a completed sale".

F-POS-19: the daily sales email defined delivered trade with its own `_DELIVERED`
predicate while every POS report used `_COMPLETED_SALE`, so the two counted
different orders and the mailed total drifted from what the console showed. The
fix makes both read ONE predicate (`pos_reports._base._COMPLETED_SALE`, which the
email now imports). This seeds a spread of orders — every channel, every status
that does and does not count — against a real Postgres and asserts the email's
grand revenue equals `sales_summary`'s net sales for the same window, and that
both equal the hand-summed total of exactly the orders that should count.

A mocked session cannot answer this: the bug lived in which rows the predicate
selects, which only a database that actually applies the WHERE clause can show.
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
from app.services.pos import daily_sales_email
from app.services.pos.pos_reports import sales_summary

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-reports-agree"
BDATE = "2026-09-01"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(
    branch_id, *, source, status, total, pos_status=None, channel=None, ref=None
):
    # A closed counter check must carry a closed_at (ck_orders_closed_has_closed_at).
    closed_at = (
        datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
        if pos_status == "closed"
        else None
    )
    return Order(
        order_number=f"RA-{uuid.uuid4().hex[:14]}",
        email="pytest-reports@example.com",
        source=source,
        branch_id=branch_id,
        is_pos=True,
        business_date=BDATE,
        status=status,
        pos_status=pos_status,
        closed_at=closed_at,
        aggregator_channel=channel,
        external_reference=ref,
        delivery_method=DeliveryMethodEnum.DELIVERY,
        subtotal=Decimal(str(total)),
        total=Decimal(str(total)),
    )


@pytest.fixture
async def seeded(engine):
    """One branch, orders spanning every channel and both counted/uncounted states.

    Returns (branch_id, expected_total) where expected_total is the hand-summed
    `total` of exactly the orders a completed-sale predicate should keep.
    """
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
        counted = [
            # Counter check, closed → counts.
            _order(
                branch.id,
                source="cashier",
                status=S.DELIVERED.value,
                pos_status="closed",
                total="100.00",
            ),
            # Website order delivered → counts.
            _order(branch.id, source="online", status=S.DELIVERED.value, total="50.00"),
            # Aggregator out-for-delivery → counts (parcel left the counter).
            _order(
                branch.id,
                source="aggregator",
                status=S.OUT_FOR_DELIVERY.value,
                channel="Talabat",
                ref=f"T-{uuid.uuid4().hex[:8]}",
                total="70.00",
            ),
            # Aggregator delivered → counts.
            _order(
                branch.id,
                source="aggregator",
                status=S.DELIVERED.value,
                channel="Careem",
                ref=f"C-{uuid.uuid4().hex[:8]}",
                total="30.00",
            ),
        ]
        uncounted = [
            # Counter check still open → not a sale yet.
            _order(
                branch.id,
                source="cashier",
                status=S.CONFIRMED.value,
                pos_status="active",
                total="999.00",
            ),
            # Counter check voided → excluded.
            _order(
                branch.id,
                source="cashier",
                status=S.CANCELLED.value,
                pos_status="void",
                total="999.00",
            ),
            # Website order not yet delivered → not counted.
            _order(
                branch.id, source="online", status=S.CONFIRMED.value, total="999.00"
            ),
            # Website order cancelled → not counted.
            _order(
                branch.id, source="online", status=S.CANCELLED.value, total="999.00"
            ),
            # Aggregator order still preparing → not counted.
            _order(
                branch.id,
                source="aggregator",
                status=S.CONFIRMED.value,
                channel="Noon",
                ref=f"N-{uuid.uuid4().hex[:8]}",
                total="999.00",
            ),
        ]
        for o in counted + uncounted:
            db.add(o)
        await db.commit()
        branch_id = branch.id

    # 100 counter + 50 website + 70 aggregator-OFD + 30 aggregator-delivered.
    expected = Decimal("250.00")

    yield branch_id, expected

    async with Session() as db:
        await db.execute(Order.__table__.delete().where(Order.branch_id == branch_id))
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.commit()


async def test_email_total_equals_sales_summary_total(seeded, engine):
    branch_id, expected = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        summary = await sales_summary(
            db, branch_id=branch_id, date_from=BDATE, date_to=BDATE
        )
        report = await daily_sales_email.build(
            db, date_from=BDATE, date_to=BDATE, branch_id=branch_id
        )

    # The email's grand revenue: every (branch, channel) cell's order total.
    email_revenue = sum(
        (cell.revenue for row in report.rows for cell in row.cells.values()),
        Decimal("0"),
    )

    assert summary["net_sales"] == expected, "sales summary counted the wrong orders"
    assert email_revenue == expected, "the email counted the wrong orders"
    # The whole point of F-POS-19: inbox and console cannot disagree.
    assert email_revenue == summary["net_sales"]
    # And the excluded orders (each 999.00) never leaked into either total.
    assert summary["net_sales"] < Decimal("999.00")
