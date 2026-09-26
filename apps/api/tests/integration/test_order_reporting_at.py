"""
`orders.reporting_at` — the generated column every date-bucketed report reads.

Postgres computes it (migration 294), so only a real database can say it is
right: a custom order counts on its delivered date, else its promised date, else
when it was taken; every other channel on its creation. It also pins the ORM
half — the column changes under an UPDATE, and reading it back after a flush
must not lazy-load (which raises MissingGreenlet under asyncpg).

Runs only when `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


async def test_reporting_at_follows_the_hand_over_for_custom_orders_only():
    from sqlalchemy import delete as sql_delete
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.branch import Branch
    from app.models.inventory import Warehouse
    from app.models.order import Order, OrderStatusEnum
    from app.models.pos_order import OrderSourceEnum

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"RPT-{uuid.uuid4().hex[:8]}"

    branch = Branch(name=marker, reference=marker)
    async with Session() as s:
        s.add(branch)
        await s.flush()
        s.add(Warehouse(branch_id=branch.id, name=marker, is_default=True))
        await s.commit()

    def _order(number: str, source: str) -> Order:
        return Order(
            order_number=f"{marker}-{number}",
            email="",
            delivery_method="pickup",
            status=OrderStatusEnum.CONFIRMED,
            source=source,
            branch_id=branch.id,
            subtotal=Decimal("100"),
            total=Decimal("100"),
            vat_amount=Decimal("0"),
            total_excl_vat=Decimal("100"),
            vat_rate=Decimal("0"),
            discount_amount=Decimal("0"),
        )

    promised = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)
    delivered = datetime(2026, 10, 4, 15, 30, tzinfo=timezone.utc)
    custom = _order("C", OrderSourceEnum.CUSTOM.value)
    online = _order("W", OrderSourceEnum.ONLINE.value)
    try:
        async with Session() as s:
            s.add_all([custom, online])
            await s.flush()
            # Nothing promised or delivered yet: when it was taken.
            assert custom.reporting_at == custom.created_at
            assert online.reporting_at == online.created_at

            custom.promised_at = promised
            online.promised_at = promised
            await s.flush()
            # Read straight after the UPDATE flush: eager defaults brought the
            # regenerated value back, so this is not a lazy load.
            assert custom.reporting_at == promised
            assert online.reporting_at == online.created_at

            custom.delivered_at = delivered
            online.delivered_at = delivered + timedelta(days=1)
            await s.flush()
            assert custom.reporting_at == delivered
            assert online.reporting_at == online.created_at
            await s.commit()

        # Migration 294's CHECK: a typo'd channel is an error, not a row that
        # silently falls out of every channel's figures.
        async with Session() as s:
            s.add(_order("X", "webiste"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()
    finally:
        async with Session() as s:
            await s.execute(sql_delete(Order).where(Order.branch_id == branch.id))
            await s.execute(
                sql_delete(Warehouse).where(Warehouse.branch_id == branch.id)
            )
            await s.execute(sql_delete(Branch).where(Branch.id == branch.id))
            await s.commit()
        await engine.dispose()
