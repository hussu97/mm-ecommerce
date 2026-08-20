"""
A checkout nobody finished is closed out, and is not called a payment failure.

MM-20260820-001: a customer opened the Stripe payment page at 05:16, went back,
and re-ordered the same basket as -002 at 05:28 — which was paid, made and
delivered. The first order sat at `created` with its session `open`, and would
have sat there forever: `checkout.session.expired` mapped to `UNHANDLED`, and
nothing else looks at a stale checkout.

That is not inert. `_redemptions_by` and `orders_placed_by` exclude *cancelled*
orders and nothing else, so the abandoned row went on holding one of that
customer's three redemptions of the NEW code and one of their three "first
orders". For a stock product it would also have held the stock the checkout took
off the shelf at write time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.order import OrderStatusEnum
from app.services import order_lifecycle, payment_service
from app.services.providers.base import PaymentEventType
from app.services.providers.stripe_provider import _EVENT_TYPES

NOW = datetime(2026, 8, 20, 5, 16, tzinfo=timezone.utc)


def _order(status=OrderStatusEnum.CREATED, **overrides):
    order = SimpleNamespace(
        id=uuid.uuid4(),
        order_number="MM-20260820-001",
        status=status,
        payment_provider="stripe",
        # The Checkout Session id, which is what an unpaid order carries. A
        # `pi_…` here would read as paid.
        payment_id="cs_live_a1mX5TWmeuxacNLmehahizoARTbyfXpn93PvzITwLjY8HiAVaXmldrjPe8",
        payment_transactions=[],
        created_at=NOW,
    )
    for key, value in overrides.items():
        setattr(order, key, value)
    return order


@pytest.fixture
def moved(monkeypatch):
    """`transition`, recorded rather than run."""
    calls: list = []

    async def transition(db, order, new_status, **kwargs):
        calls.append(new_status)
        order.status = new_status
        return True

    monkeypatch.setattr(order_lifecycle, "transition", transition)
    return calls


def test_stripe_expiry_is_its_own_event_not_a_failure():
    """
    A card that was refused and a page that was closed are different facts. The
    second must not send the payment-failed email or write a decline story onto
    an order that has none.
    """
    assert _EVENT_TYPES["checkout.session.expired"] is PaymentEventType.EXPIRED
    assert PaymentEventType.EXPIRED is not PaymentEventType.FAILED


async def test_an_abandoned_checkout_is_cancelled(moved):
    """
    Cancelled, so `_consequences` gives back the stock, and so the promo counts
    — which exclude cancelled orders and nothing else — stop counting it.
    """
    order = _order()

    assert await payment_service._handle_checkout_expired(None, order)

    assert moved == [OrderStatusEnum.CANCELLED]


async def test_an_expiry_after_payment_is_ignored(moved):
    """
    A late duplicate, not a reversal. Stripe can expire a session whose payment
    intent succeeded on another attempt, and `cancelled` is terminal — restocking
    a box somebody bought is not recoverable through the console.
    """
    order = _order(status=OrderStatusEnum.CONFIRMED)

    assert not await payment_service._handle_checkout_expired(None, order)
    assert moved == []


async def test_an_order_that_reads_as_paid_is_left_for_a_person(moved):
    """
    Still `created`, but the money moved — a confirmation webhook we missed,
    which has happened before (an SDK upgrade threw on every
    `payment_intent.succeeded` for three days). The status is our record of the
    money and this is exactly the case where our record is the thing that is
    wrong, so the paid check is asked separately.
    """
    order = _order(payment_id="pi_3U6OXTGhm8LV2f2e01xkiI4S")

    assert not await payment_service._handle_checkout_expired(None, order)
    assert moved == []


# ── the backstop ──────────────────────────────────────────────────────────────


class _Db:
    """Hands back one page of stale orders and remembers nothing else."""

    def __init__(self, orders):
        self.orders = orders

    async def execute(self, _statement):
        rows = self.orders
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows),
        )


async def test_the_sweep_cancels_what_the_webhook_never_reached(moved):
    """
    The backstop exists for the delivery that never landed — and for every order
    written before the event was mapped at all, which is the case that put
    MM-20260820-001 there.
    """
    stale = _order()

    assert await payment_service.expire_stale_checkouts(_Db([stale])) == [
        "MM-20260820-001"
    ]
    assert moved == [OrderStatusEnum.CANCELLED]


async def test_the_sweep_waits_out_the_gateway():
    """
    Stripe expires a session at 24 hours and says so itself. Sweeping earlier
    than that would cancel checkouts the gateway still considers live — and a
    customer coming back to a payment link that still works.
    """
    assert payment_service._ABANDONED_AFTER >= timedelta(hours=24)
