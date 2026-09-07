"""
A second successful charge on an already-paid order is money taken twice.

It used to be logged at INFO and dropped. Now the second, *distinct* payment is
shouted about, recorded as its own `payment_transactions` row so it is visible
in admin, and refunded automatically — the customer already paid once (F-ORD-11).

A mere re-delivery of the same success (the same `pi_…`, which every gateway
retries) is not that, and stays the quiet no-op it always was.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.order import OrderStatusEnum
from app.models.payment_transaction import PaymentTransactionStatusEnum
from app.services.payments import payment_service
from app.services.providers.base import GatewayEvent, GatewayRefund, PaymentEventType


class _Attempt:
    def __init__(self, **kw):
        self.gateway = "stripe"
        self.session_id = None
        self.payment_id = None
        self.status = PaymentTransactionStatusEnum.PENDING.value
        self.raw_status = None
        self.error_code = None
        self.error_message = None
        self.failure_reason = None
        self.refund_id = None
        self.__dict__.update(kw)

    @property
    def is_settled(self) -> bool:
        return self.status == PaymentTransactionStatusEnum.SUCCEEDED.value


class _StubProvider:
    def __init__(self, event):
        self._event = event
        self.refund = AsyncMock(
            return_value=GatewayRefund(
                refund_id="re_dup_1", amount=Decimal("125.00"), status="completed"
            )
        )

    def parse_webhook(self, payload, headers):
        return self._event


def _make_event(**kw) -> GatewayEvent:
    return GatewayEvent(
        event_id=kw.pop("event_id", "evt_dup"),
        event_type=kw.pop("event_type", PaymentEventType.SUCCEEDED),
        raw_type=kw.pop("raw_type", "payment_intent.succeeded"),
        **kw,
    )


@pytest.fixture
def order():
    return SimpleNamespace(
        id="order-uuid",
        order_number="MM-20260808-001",
        email="c@example.com",
        total=Decimal("125.00"),
        currency="AED",
        status=OrderStatusEnum.CONFIRMED,
        payment_provider="stripe",
        payment_method="card",
        payment_id="pi_1",
        payment_transactions=[
            _Attempt(
                session_id="cs_1",
                payment_id="pi_1",
                status=PaymentTransactionStatusEnum.SUCCEEDED.value,
            )
        ],
        refunded_amount=Decimal("0"),
        refunded_at=None,
        source="online",
        branch_id="branch-uuid",
        check_number=None,
    )


@pytest.fixture
def db():
    session = MagicMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture(autouse=True)
def wired(monkeypatch, order):
    monkeypatch.setattr(payment_service, "_load_order", AsyncMock(return_value=order))
    monkeypatch.setattr(
        payment_service.order_service, "to_response", AsyncMock(return_value=object())
    )


async def test_a_second_distinct_charge_is_recorded_and_refunded(
    db, order, monkeypatch, caplog
):
    provider = _StubProvider(
        _make_event(
            order_number="MM-20260808-001",
            payment_id="pi_2",  # a NEW intent — genuinely a second charge
            amount_captured=12500,
        )
    )
    monkeypatch.setattr(
        payment_service.payment_gateway_router, "PROVIDERS", {"stripe": provider}
    )

    with caplog.at_level("CRITICAL"):
        await payment_service.handle_webhook(db, "stripe", b"{}", {})

    # Recorded as its own row, visible in admin.
    duplicates = [t for t in order.payment_transactions if t.payment_id == "pi_2"]
    assert len(duplicates) == 1
    # Auto-refunded, with the duplicate's own handle.
    provider.refund.assert_awaited_once()
    assert provider.refund.await_args.kwargs["payment_id"] == "pi_2"
    assert provider.refund.await_args.kwargs["amount"] == Decimal("125.00")
    assert duplicates[0].refund_id == "re_dup_1"
    assert "DOUBLE PAYMENT" in caplog.text


async def test_a_redelivery_of_the_same_charge_is_a_quiet_no_op(
    db, order, monkeypatch, caplog
):
    provider = _StubProvider(
        _make_event(
            order_number="MM-20260808-001",
            payment_id="pi_1",  # the SAME intent already settled
            amount_captured=12500,
        )
    )
    monkeypatch.setattr(
        payment_service.payment_gateway_router, "PROVIDERS", {"stripe": provider}
    )

    with caplog.at_level("CRITICAL"):
        await payment_service.handle_webhook(db, "stripe", b"{}", {})

    assert len(order.payment_transactions) == 1
    provider.refund.assert_not_awaited()
    assert "DOUBLE PAYMENT" not in caplog.text
