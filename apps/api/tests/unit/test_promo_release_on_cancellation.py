"""
A cancelled order gives its promo redemption back (F-ORD-5).

`_persist_order` increments `promo_codes.current_uses` when a website order is
written, and nothing ever decremented it. So a coupon capped at `max_uses` was
exhausted by orders that never happened — 200 abandoned checkouts could exhaust
a month-long campaign, and every real customer after them was told the code was
"fully claimed".

`order_lifecycle._release_promo_use`, called from `_consequences` when an online
order reaches `cancelled`, hands the redemption back — `GREATEST(uses - 1, 0)` —
once per order, guarded by `orders.promo_released_at`.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models.pos_order import OrderSourceEnum
from app.services.orders import order_lifecycle

# ── the guards: who does NOT release ──────────────────────────────────────────


def _order(**over):
    base = dict(
        source=OrderSourceEnum.ONLINE.value,
        promo_code_used="WELCOME15",
        promo_released_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_an_order_with_no_code_releases_nothing():
    db = AsyncMock()
    order = _order(promo_code_used=None)
    await order_lifecycle._release_promo_use(db, order)
    db.execute.assert_not_awaited()
    assert order.promo_released_at is None


async def test_a_non_website_order_is_not_released():
    """Only `_persist_order` (a website checkout) increments `current_uses`, so
    only a website order has a redemption to give back. An aggregator order that
    happens to carry a code never took one."""
    db = AsyncMock()
    order = _order(source=OrderSourceEnum.AGGREGATOR.value)
    await order_lifecycle._release_promo_use(db, order)
    db.execute.assert_not_awaited()


async def test_an_already_released_order_does_not_release_twice():
    db = AsyncMock()
    order = _order(promo_released_at="2026-09-01T00:00:00Z")
    await order_lifecycle._release_promo_use(db, order)
    db.execute.assert_not_awaited()


async def test_a_website_order_with_a_code_releases_and_stamps():
    db = AsyncMock()
    order = _order()
    await order_lifecycle._release_promo_use(db, order)
    db.execute.assert_awaited_once()
    assert order.promo_released_at is not None


# ── against a real database ───────────────────────────────────────────────────

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

db_required = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


@db_required
class TestAgainstRealRows:
    @pytest.fixture
    async def engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(DATABASE_URL)
        yield engine
        await engine.dispose()

    async def test_the_use_is_returned_once_and_floors_at_zero(self, engine):
        from decimal import Decimal

        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import Branch, Order
        from app.models.inventory import Warehouse
        from app.models.order import DeliveryMethodEnum, OrderStatusEnum
        from app.models.promo_code import DiscountTypeEnum, PromoCode

        tag = uuid.uuid4().hex[:10]
        code = f"REL{tag.upper()}"
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                branch = Branch(name=f"rel-{tag}", reference=f"rel-{tag}")
                db.add(branch)
                await db.flush()
                db.add(Warehouse(branch_id=branch.id, name="D", is_default=True))
                db.add(
                    PromoCode(
                        code=code,
                        discount_type=DiscountTypeEnum.PERCENTAGE,
                        discount_value=Decimal("15"),
                        max_uses=100,
                        current_uses=2,
                        is_active=True,
                    )
                )
                order = Order(
                    order_number=f"REL-{uuid.uuid4().hex[:12]}",
                    email="rel@example.com",
                    source="online",
                    branch_id=branch.id,
                    status=OrderStatusEnum.CREATED,
                    delivery_method=DeliveryMethodEnum.PICKUP,
                    subtotal=Decimal("100.00"),
                    total=Decimal("85.00"),
                    delivery_fee=Decimal("0.00"),
                    promo_code_used=code,
                )
                db.add(order)
                await db.commit()
                order_id, branch_id = order.id, branch.id

            async def _uses() -> int:
                async with Session() as db:
                    return (
                        await db.execute(
                            select(PromoCode.current_uses).where(PromoCode.code == code)
                        )
                    ).scalar_one()

            # First release: 2 → 1, and the order is stamped.
            async with Session() as db:
                order = await db.get(Order, order_id)
                await order_lifecycle._release_promo_use(db, order)
                await db.commit()
            assert await _uses() == 1

            async with Session() as db:
                stamped = (
                    await db.execute(
                        select(Order.promo_released_at).where(Order.id == order_id)
                    )
                ).scalar_one()
                assert stamped is not None

            # Calling again on the same (now-stamped) order does nothing more.
            async with Session() as db:
                order = await db.get(Order, order_id)
                await order_lifecycle._release_promo_use(db, order)
                await db.commit()
            assert await _uses() == 1

            # And a code already at zero floors rather than going negative.
            async with Session() as db:
                await db.execute(
                    PromoCode.__table__.update()
                    .where(PromoCode.code == code)
                    .values(current_uses=0)
                )
                order2 = Order(
                    order_number=f"REL2-{uuid.uuid4().hex[:11]}",
                    email="rel2@example.com",
                    source="online",
                    branch_id=branch_id,
                    status=OrderStatusEnum.CREATED,
                    delivery_method=DeliveryMethodEnum.PICKUP,
                    subtotal=Decimal("100.00"),
                    total=Decimal("85.00"),
                    delivery_fee=Decimal("0.00"),
                    promo_code_used=code,
                )
                db.add(order2)
                await db.flush()
                await order_lifecycle._release_promo_use(db, order2)
                await db.commit()
            assert await _uses() == 0

            async with Session() as db:
                await db.execute(delete(Order).where(Order.branch_id == branch_id))
                await db.execute(delete(PromoCode).where(PromoCode.code == code))
                await db.execute(delete(Branch).where(Branch.id == branch_id))
                await db.commit()
        finally:
            await engine.dispose()
