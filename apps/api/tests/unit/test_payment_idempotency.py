"""
A retried payment is not a second payment.

`RegisterModel.pay` is three calls — record, close, print — over a 15-second
timeout. A payment the server genuinely took can time out on the way back, and
the cashier, looking at a check that still reads unpaid, takes the money again.

The only guard was `amount > outstanding`, which covers a single tender and
nothing else: on a split payment the replayed first half is a legitimately
smaller amount against a legitimately lower balance, so it passes cleanly.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ConflictError
from app.models.pos_order import OrderPayment
from app.services import pos_order_service

pytestmark = pytest.mark.asyncio

KEY = "0B8B6E3C-3B5C-4E7A-9E0A-0F5B2C1D4E6F"


def _order(order_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id,
        order_number="POS-B001-2026-08-06-0042",
        balance_due=Decimal("100.00"),
        business_date="2026-08-06",
        till_id=None,
        payments=[],
    )


def _db_returning(existing: OrderPayment | None):
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = existing
    db.execute = AsyncMock(return_value=result)
    return db


class TestPaymentIdempotency:
    async def test_replay_returns_the_original_payment(self):
        order_id = uuid.uuid4()
        original = OrderPayment(
            order_id=order_id,
            amount=Decimal("50.00"),
            idempotency_key=KEY,
        )
        db = _db_returning(original)

        returned = await pos_order_service.record_payment(
            db,
            order=_order(order_id),
            user=SimpleNamespace(id=uuid.uuid4()),
            payment_method_id=uuid.uuid4(),
            amount=Decimal("50.00"),
            idempotency_key=KEY,
        )

        assert returned is original
        db.add.assert_not_called(), "a replay wrote a second payment row"

    async def test_key_reused_on_a_different_order_is_refused(self):
        """
        Not merely ignored: the same key against a different order means the
        client is confused about which check it is paying, and taking the money
        against either would be a guess.
        """
        original = OrderPayment(
            order_id=uuid.uuid4(),
            amount=Decimal("50.00"),
            idempotency_key=KEY,
        )
        db = _db_returning(original)

        with pytest.raises(ConflictError):
            await pos_order_service.record_payment(
                db,
                order=_order(uuid.uuid4()),
                user=SimpleNamespace(id=uuid.uuid4()),
                payment_method_id=uuid.uuid4(),
                amount=Decimal("50.00"),
                idempotency_key=KEY,
            )

    async def test_no_key_still_reaches_the_payment_method_lookup(self):
        """
        A client that sends no key behaves exactly as before — the guard is
        opt-in, so nothing that predates it changes.
        """
        db = _db_returning(None)
        db.get = AsyncMock(return_value=None)  # payment method not found

        from app.core.exceptions import BadRequestError

        with pytest.raises(BadRequestError, match="Payment method not available"):
            await pos_order_service.record_payment(
                db,
                order=_order(uuid.uuid4()),
                user=SimpleNamespace(id=uuid.uuid4()),
                payment_method_id=uuid.uuid4(),
                amount=Decimal("50.00"),
            )
        (
            db.execute.assert_not_called(),
            ("no key was sent, so no idempotency lookup should have happened"),
        )
