"""
A voided check must not accept a payment (F-POS-10).

`record_payment` was the one mutation with no open-check guard, and `void_order`
left `balance_due` positive — so cash could be taken into a cancelled sale and it
would land in the drawer. The fix guards non-refund payments with `_assert_open`.
A refund is the one payment that must still work once a check is settled, so it
is exempt.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BadRequestError, ConflictError
from app.services.pos import pos_order_service

pytestmark = pytest.mark.asyncio


def _void_order():
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="POS-VOID-0001",
        pos_status="void",
        payments=[],
        balance_due=Decimal("50.00"),
    )


def _db():
    # `db.execute` serves the FOR UPDATE lock select; its value is irrelevant
    # because `get_order` is monkeypatched to hand back the order under test.
    return SimpleNamespace(execute=AsyncMock(return_value=MagicMock()), get=AsyncMock())


async def test_paying_a_voided_check_is_refused(monkeypatch):
    order = _void_order()
    monkeypatch.setattr(pos_order_service, "get_order", AsyncMock(return_value=order))

    with pytest.raises(ConflictError, match="void"):
        await pos_order_service.record_payment(
            _db(),
            order=order,
            user=SimpleNamespace(id=uuid.uuid4()),
            payment_method_id=uuid.uuid4(),
            amount=Decimal("50.00"),
        )


async def test_a_refund_on_a_voided_check_is_not_blocked_by_the_open_guard(monkeypatch):
    order = _void_order()
    monkeypatch.setattr(pos_order_service, "get_order", AsyncMock(return_value=order))

    db = _db()
    # No payment method → BadRequestError, which is only reachable once the
    # open-check guard has been passed. So proving we get *here* proves a refund
    # was not blocked by the void status.
    db.get = AsyncMock(return_value=None)

    with pytest.raises(BadRequestError, match="Payment method"):
        await pos_order_service.record_payment(
            db,
            order=order,
            user=SimpleNamespace(id=uuid.uuid4()),
            payment_method_id=uuid.uuid4(),
            amount=Decimal("10.00"),
            is_refund=True,
        )
