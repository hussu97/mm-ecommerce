"""A promotion's usage count, against a real Postgres.

`auto_promotion_service.usage_counts` decides when a limited promotion stops
being offered, so it has to count exactly the orders that used one up: a
completed sale counts once, however many lines a category-scoped promotion
discounted, and a draft, an open check or a void never counts.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.marketing import Promotion
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.pos_order import DiscountSourceEnum, OrderDiscount
from app.services.pos import auto_promotion_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "promo-usage-test"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(branch_id, *, status, pos_status, discounts: int = 1):
    closed = pos_status == "closed"
    return Order(
        order_number=f"PU-{uuid.uuid4().hex[:14]}",
        email="promo@example.com",
        source="cashier",
        branch_id=branch_id,
        is_pos=True,
        business_date="2026-09-25",
        status=status,
        pos_status=pos_status,
        closed_at=datetime(2026, 9, 25, 12, tzinfo=timezone.utc) if closed else None,
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal("100"),
        total=Decimal("50"),
    )


async def test_usage_counts_only_completed_orders_once_each(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:8]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))
        promo = Promotion(
            name=f"{MARKER} 50 off",
            reward="fixed_off_order",
            reward_value=Decimal("50"),
            trigger="spend",
            trigger_value=Decimal("100"),
            sources=["cashier"],
            coupon_branch_ids=[branch.id],
            usage_limit=4,
        )
        db.add(promo)
        await db.flush()

        DELIVERED = OrderStatusEnum.DELIVERED.value
        cases = [
            # (status, pos_status, discount rows, counts?)
            (DELIVERED, "closed", 1, True),
            (DELIVERED, "closed", 3, True),  # category-scoped: 3 lines, 1 use
            (OrderStatusEnum.CONFIRMED.value, "active", 1, False),  # still open
            (OrderStatusEnum.CANCELLED.value, "void", 1, False),
            (OrderStatusEnum.CREATED.value, "draft", 1, False),
        ]
        for status, pos_status, rows, _ in cases:
            order = _order(branch.id, status=status, pos_status=pos_status)
            db.add(order)
            await db.flush()
            for _n in range(rows):
                db.add(
                    OrderDiscount(
                        order_id=order.id,
                        source=DiscountSourceEnum.PROMOTION.value,
                        name=promo.name,
                        reference_id=promo.id,
                        is_percentage=False,
                        value=Decimal("50"),
                        amount=Decimal("50"),
                    )
                )
        await db.commit()
        promo_id, branch_id = promo.id, branch.id

    try:
        async with Session() as db:
            used = await auto_promotion_service.usage_counts(db, [promo_id])
            assert used == {promo_id: 2}
            promo = await db.get(Promotion, promo_id)
            assert await auto_promotion_service.exhausted_ids(db, [promo]) == set()
            promo.usage_limit = 2
            assert await auto_promotion_service.exhausted_ids(db, [promo]) == {promo_id}
            assert await auto_promotion_service.without_exhausted(db, [promo]) == []
            await db.rollback()
    finally:
        async with Session() as db:
            await db.execute(text("SET session_replication_role = 'replica'"))
            order_ids = select(Order.id).where(Order.branch_id == branch_id)
            await db.execute(
                delete(OrderDiscount).where(OrderDiscount.order_id.in_(order_ids))
            )
            await db.execute(delete(Order).where(Order.branch_id == branch_id))
            await db.execute(delete(Promotion).where(Promotion.id == promo_id))
            await db.execute(delete(Warehouse).where(Warehouse.branch_id == branch_id))
            await db.execute(delete(Branch).where(Branch.id == branch_id))
            await db.commit()
