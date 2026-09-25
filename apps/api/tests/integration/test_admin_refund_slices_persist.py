"""
Admin refunds must survive the commit, not just the mock.

`issue_admin_refund` books every refund as its own `payment_transactions` row
(`_record_refund_slice`). That row used to copy the settled attempt's
`payment_id`, and `uq_payment_transactions_gateway_payment` is unique on
`(gateway, payment_id)` — so the very first admin refund raised an
`IntegrityError` at flush, *after* the gateway had already sent the money
back. The customer was refunded and the order said nothing had happened.
(Before it got that far it crashed on `order.currency`, a column `Order` has
never had — this test builds a real `Order`, so both are caught.)

Every unit test of the refund path mocks the session, which is exactly why it
went unseen: only Postgres enforces the index. So this runs against a real one —
three refunds on one order (partial, partial, the rest), each committed.

Runs only when `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.providers.base import GatewayRefund

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


async def test_three_admin_refunds_on_one_order_all_persist(monkeypatch):
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.orm import selectinload

    from app.models.branch import Branch
    from app.models.inventory import Warehouse
    from app.models.order import Order, OrderStatusEnum
    from app.models.payment_transaction import (
        PaymentTransaction,
        PaymentTransactionStatusEnum,
    )
    from app.models.pos_order import OrderSourceEnum
    from app.services.orders import order_lifecycle
    from app.services.payments import payment_gateway_router, payment_service

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"RFND-{uuid.uuid4().hex[:8]}"

    # A committed branch needs its one default warehouse (deferred trigger, mig 186).
    branch = Branch(name=marker, reference=marker)
    async with Session() as s:
        s.add(branch)
        await s.flush()
        s.add(Warehouse(branch_id=branch.id, name=marker, is_default=True))
        await s.commit()

    payment_id = f"pi_{marker}"
    order = Order(
        order_number=marker,
        email="c@example.com",
        locale="en",
        delivery_method="delivery",
        order_type="delivery",
        status=OrderStatusEnum.DELIVERED,
        source=OrderSourceEnum.ONLINE.value,
        branch_id=branch.id,
        payment_method="card",
        payment_provider="stripe",
        payment_id=payment_id,
        subtotal=Decimal("100"),
        total=Decimal("100"),
        vat_amount=Decimal("0"),
        total_excl_vat=Decimal("100"),
        vat_rate=Decimal("0"),
        discount_amount=Decimal("0"),
    )
    async with Session() as s:
        s.add(order)
        await s.flush()
        s.add(
            PaymentTransaction(
                order_id=order.id,
                gateway="stripe",
                session_id=f"cs_{marker}",
                payment_id=payment_id,
                status=PaymentTransactionStatusEnum.SUCCEEDED.value,
                amount=Decimal("100"),
                currency="AED",
            )
        )
        await s.commit()

    issued: list[Decimal] = []

    async def fake_refund(*, payment_id, amount, idempotency_key, **_):
        issued.append(amount)
        return GatewayRefund(
            refund_id=f"re_{len(issued)}", amount=amount, status="completed"
        )

    monkeypatch.setitem(
        payment_gateway_router.PROVIDERS, "stripe", SimpleNamespace(refund=fake_refund)
    )
    # The full-refund status move is not what this pins, and its consequences
    # (restock, promo release, emails) reach well outside the payment tables.
    monkeypatch.setattr(order_lifecycle, "transition", AsyncMock(return_value=True))

    async def load(s):
        return (
            await s.execute(
                select(Order)
                .options(selectinload(Order.payment_transactions))
                .where(Order.id == order.id)
            )
        ).scalar_one()

    try:
        for amount in (Decimal("20.00"), Decimal("30.00"), Decimal("50.00")):
            async with Session() as s:
                await payment_service.issue_admin_refund(
                    s, await load(s), amount=amount
                )
                await s.commit()

        async with Session() as s:
            stored = await load(s)
            slices = [
                t
                for t in stored.payment_transactions
                if t.status == PaymentTransactionStatusEnum.REFUNDED.value
            ]
            assert issued == [Decimal("20.00"), Decimal("30.00"), Decimal("50.00")]
            assert Decimal(str(stored.refunded_amount)) == Decimal("100.00")
            assert sorted(t.refund_id for t in slices) == ["re_1", "re_2", "re_3"]
            # The settled attempt is untouched and still the one refunds find.
            settled = [t for t in stored.payment_transactions if t.is_settled]
            assert [t.payment_id for t in settled] == [payment_id]
            # And the refund webhook that follows each one is recognised as
            # already booked, so it is not recorded a second time.
            for refund_id in ("re_1", "re_2", "re_3"):
                assert await payment_service._refund_already_recorded(
                    s, stored, refund_id
                )
    finally:
        async with Session() as s:
            await s.execute(
                sql_delete(PaymentTransaction).where(
                    PaymentTransaction.order_id == order.id
                )
            )
            obj = await s.get(Order, order.id)
            if obj:
                await s.delete(obj)
            await s.execute(
                sql_delete(Warehouse).where(Warehouse.branch_id == branch.id)
            )
            b = await s.get(Branch, branch.id)
            if b:
                await s.delete(b)
            await s.commit()
        await engine.dispose()
