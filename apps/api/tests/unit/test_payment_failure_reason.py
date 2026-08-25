"""
The failed-payment reason, from the attempt row to the customer-facing response.

`_apply_payment_failure` is the seam between what the gateway told us (stored on
`payment_transactions`) and the one thing the storefront shows. The rules worth
pinning: it speaks only while the order is still `payment_failed`, it reads the
*latest* failed attempt, and it exposes both the normalised code and the raw
message so the client can prefer the first and fall back to the second.
"""

from __future__ import annotations

import datetime as dt

from app.models.order import Order, OrderStatusEnum
from app.models.payment_transaction import (
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from app.schemas.order import OrderResponse
from app.services.orders.order_service import _apply_payment_failure


def _response() -> OrderResponse:
    """A blank response object — only the two fields under test are read back."""
    return OrderResponse.model_construct()


def _attempt(
    *,
    status: str,
    reason: str | None = None,
    message: str | None = None,
    minute: int = 0,
) -> PaymentTransaction:
    return PaymentTransaction(
        gateway="stripe",
        amount=100,
        status=status,
        failure_reason=reason,
        error_message=message,
        created_at=dt.datetime(2026, 8, 24, 12, minute, tzinfo=dt.timezone.utc),
    )


def test_a_failed_order_exposes_its_latest_failed_reason():
    order = Order(status=OrderStatusEnum.PAYMENT_FAILED)
    order.payment_transactions = [
        _attempt(status="failed", reason="expired_card", minute=0),
        _attempt(status="failed", reason="insufficient_funds", minute=5),
    ]
    response = _response()

    _apply_payment_failure(order, response)

    assert response.payment_failure_reason == "insufficient_funds"


def test_the_raw_message_rides_along_for_a_gateway_without_a_code():
    order = Order(status=OrderStatusEnum.PAYMENT_FAILED)
    order.payment_transactions = [
        _attempt(status="failed", reason=None, message="Ziina said no.", minute=1),
    ]
    response = _response()

    _apply_payment_failure(order, response)

    assert response.payment_failure_reason is None
    assert response.payment_failure_message == "Ziina said no."


def test_a_paid_order_says_nothing_even_if_an_earlier_attempt_failed():
    """A decline that was retried and paid must not leave a stale reason."""
    order = Order(status=OrderStatusEnum.CONFIRMED)
    order.payment_transactions = [
        _attempt(status="failed", reason="insufficient_funds", minute=0),
        _attempt(status=PaymentTransactionStatusEnum.SUCCEEDED.value, minute=5),
    ]
    response = _response()

    _apply_payment_failure(order, response)

    assert response.payment_failure_reason is None
    assert response.payment_failure_message is None


def test_an_abandoned_checkout_has_no_reason():
    order = Order(status=OrderStatusEnum.PAYMENT_FAILED)
    order.payment_transactions = [
        _attempt(status=PaymentTransactionStatusEnum.CANCELLED.value, minute=0),
    ]
    response = _response()

    _apply_payment_failure(order, response)

    assert response.payment_failure_reason is None
    assert response.payment_failure_message is None
