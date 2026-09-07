"""
Two checkouts of one per-user coupon, at the same instant, against a real DB.

`max_uses_per_user` (and `first_orders_limit`) were re-checked in the checkout
by `validate` and then trusted at the write. Between the two, a second order
from the same person could land — a double tap, two tabs — and both passed a
check made before either was written. On a new-customer coupon that is money:
the rule exists to stop one person taking the discount twice.

`promo_code_service.assert_within_per_user_limits`, called from
`_persist_order`, reads the promo row `FOR UPDATE` and re-counts under the lock,
so the second caller waits, sees the first's committed order, and is refused.

That is a question about a row lock holding across two connections, which a
mocked session cannot answer — it needs a real Postgres. `asyncio.gather` over
two independent sessions is the closest a test gets to two checkouts racing; a
short sleep after the check widens the window so that without the lock both
callers really would redeem. SKIPs unless `TEST_DATABASE_URL` is set.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError
from app.models import Branch, Order
from app.models.inventory import Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.promo_code import DiscountTypeEnum, PromoCode
from app.services import promo_code_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

TAG = uuid.uuid4().hex[:10]
EMAIL = f"race-{TAG}@example.com"
CODE = f"RACE{TAG.upper()}"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded(engine):
    """A branch, and a one-use-each coupon nobody has redeemed yet."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(name=f"race-{TAG}", reference=f"race-{TAG}")
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
        promo = PromoCode(
            code=CODE,
            discount_type=DiscountTypeEnum.PERCENTAGE,
            discount_value=Decimal("15"),
            max_uses_per_user=1,
            current_uses=0,
            is_active=True,
        )
        db.add(promo)
        await db.commit()
        ids = (branch.id, promo.id)

    yield ids

    async with Session() as db:
        await db.execute(delete(Order).where(Order.email == EMAIL))
        await db.execute(delete(PromoCode).where(PromoCode.code == CODE))
        await db.execute(delete(Branch).where(Branch.id == ids[0]))
        await db.commit()


async def test_two_concurrent_checkouts_redeem_a_one_use_code_once(engine, seeded):
    branch_id, promo_id = seeded
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def checkout_once() -> str:
        """Mirror `_persist_order`: assert the per-user rule under the lock,
        then write the order that redeems the code, then commit."""
        async with Session() as db:
            promo = await db.get(PromoCode, promo_id)
            await promo_code_service.assert_within_per_user_limits(
                db, promo, user_id=None, email=EMAIL, phone=None
            )
            # Widen the window: without the FOR UPDATE lock the second caller
            # would already have passed its check here and would also redeem.
            await asyncio.sleep(0.2)
            db.add(
                Order(
                    order_number=f"RACE-{uuid.uuid4().hex[:12]}",
                    email=EMAIL,
                    source="online",
                    branch_id=branch_id,
                    status=OrderStatusEnum.CREATED,
                    delivery_method=DeliveryMethodEnum.PICKUP,
                    subtotal=Decimal("100.00"),
                    total=Decimal("85.00"),
                    delivery_fee=Decimal("0.00"),
                    promo_code_used=CODE,
                )
            )
            await db.commit()
            return "redeemed"

    results = await asyncio.gather(
        checkout_once(), checkout_once(), return_exceptions=True
    )

    redeemed = [r for r in results if r == "redeemed"]
    refused = [r for r in results if isinstance(r, BadRequestError)]

    assert len(redeemed) == 1, f"exactly one checkout may redeem: {results}"
    assert len(refused) == 1, f"the loser is refused, not crashed: {results}"
    assert str(refused[0]) == "You have already used this code"

    # And the database carries exactly one redemption of the code.
    async with Session() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.email == EMAIL, Order.promo_code_used == CODE)
            )
        ).scalar()
        assert count == 1
