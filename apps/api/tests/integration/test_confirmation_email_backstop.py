"""`confirmation_email_backstop`: a confirmation a crash lost is sent late.

Confirmation emails now go out after the confirming transaction commits, so a
process killed between the `COMMIT` and the send loses them with the gateway's
dedup row already written — the retry will not resend. The backstop finds
storefront orders confirmed by a payment or a checkout in its window whose
journal has no row at all for an email, and sends exactly that one.

Needs a database: set TEST_DATABASE_URL (or DATABASE_URL).
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.email_log import EmailLog
from app.models.inventory import Warehouse
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum
from app.services.orders import confirmation_email_backstop as backstop

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

#: Where the sweep looks from: half an hour after the orders below confirmed,
#: inside the window (older than its ten-minute floor, younger than six hours).
LATER = timedelta(minutes=30)


@pytest.fixture
async def world():
    engine = create_async_engine(DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    marker = f"BS-{uuid.uuid4().hex[:8]}"
    async with maker() as s:
        branch = Branch(name=marker, reference=marker)
        s.add(branch)
        await s.flush()
        s.add(Warehouse(branch_id=branch.id, name=marker, is_default=True))
        await s.commit()

    numbers: list[str] = []

    async def order(
        suffix: str,
        *,
        by: str = StatusSourceEnum.PAYMENT.value,
        status: OrderStatusEnum = OrderStatusEnum.CONFIRMED,
        logged: tuple[tuple[str, str], ...] = (),
    ) -> str:
        number = f"{marker}-{suffix}"
        async with maker() as s:
            # Written straight at `confirmed` under the actor that confirmed it,
            # so the status-event capture stamps the row the sweep reads.
            with acting_as(by):
                row = Order(
                    order_number=number,
                    email="c@example.com",
                    locale="en",
                    delivery_method="pickup",
                    order_type="pickup",
                    status=OrderStatusEnum.CONFIRMED,
                    source=OrderSourceEnum.ONLINE.value,
                    branch_id=branch.id,
                    payment_method="card",
                    subtotal=Decimal("50"),
                    total=Decimal("50"),
                    vat_amount=Decimal("0"),
                    total_excl_vat=Decimal("50"),
                    vat_rate=Decimal("0"),
                    discount_amount=Decimal("0"),
                    customer_name="Sara Ali",
                    customer_phone="+971501234567",
                )
                s.add(row)
                await s.flush()
            if status != OrderStatusEnum.CONFIRMED:
                row.status = status  # the history, not the lifecycle, is under test
            for template, outcome in logged:
                s.add(
                    EmailLog(
                        template=template,
                        recipient="c@example.com",
                        subject="s",
                        order_number=number,
                        status=outcome,
                    )
                )
            await s.commit()
        numbers.append(number)
        return number

    async def owed() -> dict[str, tuple[bool, bool]]:
        async with maker() as s:
            found = await backstop.find_unsent(s, now=utcnow() + LATER)
        return {
            o.order.order_number: (o.customer, o.owner)
            for o in found
            if o.order.order_number.startswith(marker)
        }

    yield SimpleNamespace(order=order, owed=owed, maker=maker)

    async with maker() as s:
        await s.execute(delete(EmailLog).where(EmailLog.order_number.in_(numbers)))
        await s.execute(delete(Order).where(Order.order_number.in_(numbers)))
        await s.execute(delete(Warehouse).where(Warehouse.branch_id == branch.id))
        await s.execute(delete(Branch).where(Branch.id == branch.id))
        await s.commit()
    await engine.dispose()
    backstop._ATTEMPTED.clear()


async def test_finds_exactly_the_emails_that_left_no_trace(world):
    nothing = await world.order("none")
    failed_customer = await world.order(
        "failed", logged=(("order_confirmation", "failed"),)
    )
    both = await world.order(
        "both",
        logged=(
            ("order_confirmation", "sent"),
            ("owner_order_notification", "sent"),
        ),
    )
    by_checkout = await world.order("cash", by=StatusSourceEnum.CHECKOUT.value)
    by_admin = await world.order("admin", by=StatusSourceEnum.ADMIN.value)
    cancelled = await world.order("cxl", status=OrderStatusEnum.CANCELLED)

    owed = await world.owed()

    assert owed[nothing] == (True, True)
    # A failed send was attempted: that is `report_failed_sends`' business, and
    # only the owners' email — which left no row — is owed.
    assert owed[failed_customer] == (False, True)
    assert owed[by_checkout] == (True, True)
    assert both not in owed
    # An admin confirming by hand never sends the owners' email.
    assert by_admin not in owed
    assert cancelled not in owed


async def test_leaves_a_send_still_in_flight_alone(world):
    fresh = await world.order("fresh")
    async with world.maker() as s:
        found = await backstop.find_unsent(s, now=utcnow() + timedelta(minutes=2))
    assert fresh not in {o.order.order_number for o in found}


async def test_sends_what_is_owed_once(world, monkeypatch):
    number = await world.order("send")
    sent = SimpleNamespace(confirmation=AsyncMock(), owner=AsyncMock())
    monkeypatch.setattr(
        backstop.email_service, "send_order_confirmation", sent.confirmation
    )
    monkeypatch.setattr(
        backstop.email_service, "send_owner_order_notification", sent.owner
    )

    owed = [o for o in await _all(world) if o.order.order_number == number]
    assert await backstop.send(owed) == [number]
    sent.confirmation.assert_awaited_once()
    sent.owner.assert_awaited_once()

    # The mocks wrote no journal row — the case a broken `email_logs` insert
    # leaves behind. The next tick must still not send it again.
    assert number not in await world.owed()


async def _all(world):
    async with world.maker() as s:
        return await backstop.find_unsent(s, now=utcnow() + LATER)
