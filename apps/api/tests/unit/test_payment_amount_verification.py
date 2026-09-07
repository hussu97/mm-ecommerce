"""
An order is not confirmed for less money than it costs (F-ORD-19).

A gateway reports what it actually captured. If that is short of the order
total — a tampered amount, a currency mix-up, a captured-less-than-authorised
intent — confirming the order would ship goods that were not paid for. The
webhook refuses, loudly, and leaves the order where it was for a human.
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
from app.services.payments import payment_service
from app.services.providers import stripe_provider as sp
from app.services.providers.base import GatewayEvent, PaymentEventType

SECRET = "whsec_test_secret"


def _signed(body: dict) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(body).encode()
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload.decode()}", SECRET
    )
    return payload, {"stripe-signature": f"t={timestamp},v1={signature}"}


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)


# ── the amount is read off the intent ─────────────────────────────────────────


def test_the_captured_amount_is_parsed_from_amount_received():
    payload, headers = _signed(
        {
            "id": "evt_1",
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
    parsed = sp.provider.parse_webhook(payload, headers)
    assert parsed.amount_captured == 12500


# ── and the confirmation refuses to undercharge ───────────────────────────────


class _StubProvider:
    def __init__(self, event):
        self._event = event

    def parse_webhook(self, payload, headers):
        return self._event


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


def _make_event(**kw) -> GatewayEvent:
    return GatewayEvent(
        event_id=kw.pop("event_id", "evt_1"),
        event_type=kw.pop("event_type", PaymentEventType.SUCCEEDED),
        raw_type=kw.pop("raw_type", "payment_intent.succeeded"),
        **kw,
    )


async def test_an_underpaid_capture_does_not_confirm(db, order, monkeypatch, caplog):
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "PROVIDERS",
        {
            "stripe": _StubProvider(
                _make_event(
                    order_number="MM-20260808-001",
                    payment_id="pi_1",
                    amount_captured=10000,  # AED 100 against a 125 order
                )
            )
        },
    )

    with caplog.at_level("CRITICAL"):
        await payment_service.handle_webhook(db, "stripe", b"{}", {})

    assert order.status == OrderStatusEnum.CREATED
    assert "underpaid" in caplog.text.lower()


async def test_the_exact_amount_confirms(db, order, monkeypatch):
    monkeypatch.setattr(
        payment_service.payment_gateway_router,
        "PROVIDERS",
        {
            "stripe": _StubProvider(
                _make_event(
                    order_number="MM-20260808-001",
                    payment_id="pi_1",
                    amount_captured=12500,
                )
            )
        },
    )

    await payment_service.handle_webhook(db, "stripe", b"{}", {})

    assert order.status == OrderStatusEnum.CONFIRMED
