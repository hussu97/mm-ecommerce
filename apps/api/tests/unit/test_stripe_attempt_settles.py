"""
A Stripe hosted-Checkout payment must settle the row it was booked against.

The attempt is written at session creation as `{session_id: "cs_…",
payment_id: null}`, and Stripe's Payment Intent does not exist until the
customer pays. So the confirmation webhook is the only place the `pi_…` is ever
learned — and if it does not stitch onto that row, the attempt stays `pending`,
`_settled_attempt` returns nothing, and a later `refund_order` refunds zero with
no loud log (F-ORD-1).

These are driven from the *real* `StripeProvider.parse_webhook` output, not a
hand-built `GatewayEvent`: the bug lived precisely in the gap between the shape
a provider emits (`payment_intent.succeeded` carries a `pi_…` and no `cs_…`) and
the shape a fixture imagined it emitted.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import stripe

from app.core.config import settings
from app.models.order import OrderStatusEnum
from app.models.payment_transaction import PaymentTransactionStatusEnum
from app.services.payments import payment_service

SECRET = "whsec_test_secret"


def _signed(body: dict) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(body).encode()
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload.decode()}", SECRET
    )
    return payload, {"stripe-signature": f"t={timestamp},v1={signature}"}


class _Attempt:
    """A payment_transactions row whose `is_settled` tracks its status."""

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


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)


@pytest.fixture
def order():
    return SimpleNamespace(
        id="order-uuid",
        order_number="MM-20260808-001",
        email="c@example.com",
        total=Decimal("125.00"),
        status=OrderStatusEnum.CREATED,
        payment_provider="stripe",
        payment_method="card",
        payment_id=None,
        payment_transactions=[],
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
    return session


@pytest.fixture(autouse=True)
def wired(monkeypatch, order):
    """Order lookup, arrival scheduling, inventory and emails all stubbed."""
    monkeypatch.setattr(payment_service, "_load_order", AsyncMock(return_value=order))
    monkeypatch.setattr(
        payment_service.order_service, "to_response", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        payment_service.order_service, "publish_to_register", AsyncMock()
    )
    from app.services.delivery import arrival_service
    from app.services.inventory import source_event_service

    monkeypatch.setattr(arrival_service, "schedule", AsyncMock(return_value=None))
    monkeypatch.setattr(
        source_event_service, "accept_order", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_order_confirmation", AsyncMock()
    )
    monkeypatch.setattr(
        payment_service.email_service, "send_owner_order_notification", AsyncMock()
    )


async def test_a_payment_intent_succeeded_settles_the_cs_attempt(db, order):
    """
    `payment_intent.succeeded` carries the `pi_…` and no `cs_…`. It must adopt
    the id onto the one pending attempt for the gateway and settle it.
    """
    attempt = _Attempt(session_id="cs_1", payment_id=None)
    order.payment_transactions = [attempt]

    payload, headers = _signed(
        {
            "id": "evt_pi_1",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_1",
                    "amount_received": 12500,
                    "metadata": {"order_number": "MM-20260808-001"},
                }
            },
        }
    )

    await payment_service.handle_webhook(db, "stripe", payload, headers)

    assert attempt.payment_id == "pi_1"
    assert attempt.status == PaymentTransactionStatusEnum.SUCCEEDED.value
    assert order.status == OrderStatusEnum.CONFIRMED


async def test_a_checkout_session_completed_settles_the_cs_attempt(db, order):
    """
    `checkout.session.completed` carries BOTH the `cs_…` and the `pi_…`, so it
    stitches the payment id onto the attempt it already matches by session.
    """
    attempt = _Attempt(session_id="cs_1", payment_id=None)
    order.payment_transactions = [attempt]

    payload, headers = _signed(
        {
            "id": "evt_cs_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "payment_intent": "pi_1",
                    "metadata": {"order_number": "MM-20260808-001"},
                }
            },
        }
    )

    await payment_service.handle_webhook(db, "stripe", payload, headers)

    assert attempt.payment_id == "pi_1"
    assert attempt.status == PaymentTransactionStatusEnum.SUCCEEDED.value
    assert order.status == OrderStatusEnum.CONFIRMED


async def test_the_adoption_is_refused_when_two_attempts_are_pending(db, order):
    """
    Two live checkouts leave two pending rows, and a `payment_intent.succeeded`
    carrying only a `pi_…` cannot be attributed between them. The guard adopts
    only when exactly one pending attempt exists.
    """
    a = _Attempt(session_id="cs_1", payment_id=None)
    b = _Attempt(session_id="cs_2", payment_id=None)
    order.payment_transactions = [a, b]

    payload, headers = _signed(
        {
            "id": "evt_pi_2",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_9",
                    "amount_received": 12500,
                    "metadata": {"order_number": "MM-20260808-001"},
                }
            },
        }
    )

    await payment_service.handle_webhook(db, "stripe", payload, headers)

    assert a.payment_id is None
    assert b.payment_id is None
    assert a.status == PaymentTransactionStatusEnum.PENDING.value
    assert b.status == PaymentTransactionStatusEnum.PENDING.value
