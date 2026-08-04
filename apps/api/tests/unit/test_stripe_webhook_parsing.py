"""
Reading a Stripe webhook without depending on the SDK's object model.

This is a regression test for a live incident. `stripe.Webhook.construct_event`
returns typed resources, and since stripe-python 8 those no longer subclass
`dict` — so `event["data"]["object"].get("id")` raises `AttributeError: get`.
The SDK was upgraded to 15.4, every `payment_intent.succeeded` started throwing
there, the route swallowed it into a 200, and orders that customers had actually
paid for sat in `created` for three days while Stripe's dashboard showed nothing
but successful deliveries.

Two things are pinned here. The parse reads the verified JSON rather than the
SDK objects, so a minor version bump cannot change the shape under it. And the
route tells a client error apart from ours, so the next failure is loud.
"""

from __future__ import annotations

import json
import time

import pytest
import stripe

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.services.providers import stripe_provider as sp

SECRET = "whsec_test_secret"


def _event(event_type: str, obj: dict, event_id: str = "evt_test_1") -> dict:
    """The envelope Stripe actually sends, not just the parts we read."""
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": 1_754_000_000,
        "livemode": True,
        "type": event_type,
        "data": {"object": obj},
    }


def _signed(body: dict) -> tuple[bytes, str]:
    """A payload and a header Stripe's own verifier accepts."""
    payload = json.dumps(body).encode()
    # Stripe rejects anything more than five minutes old, so this has to be now.
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload.decode()}", SECRET
    )
    return payload, f"t={timestamp},v1={signature}"


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)


# ── the incident ──────────────────────────────────────────────────────────────


def test_the_sdk_object_that_broke_it_really_has_no_get():
    """
    The premise, asserted rather than remembered. If a future SDK restores dict
    behaviour this test fails and the workaround can go — and if it does not,
    nobody is tempted to "simplify" the parse back to the version that broke.
    """
    event = stripe.Event.construct_from(
        _event("payment_intent.succeeded", {"id": "pi_1", "object": "payment_intent"}),
        "sk_test",
    )
    assert not hasattr(event["data"]["object"], "get")


def test_a_succeeded_payment_yields_its_order_number():
    """The whole point: this is what confirms an order."""
    payload, header = _signed(
        _event(
            "payment_intent.succeeded",
            {
                "id": "pi_live_123",
                "metadata": {"order_number": "MM-20260804-001", "order_id": "abc"},
            },
        )
    )
    parsed = sp.provider.handle_webhook(payload, header)

    assert parsed["event_type"] == "payment_intent.succeeded"
    assert parsed["order_number"] == "MM-20260804-001"
    assert parsed["payment_intent_id"] == "pi_live_123"
    assert parsed["event_id"] == "evt_test_1"


def test_a_failed_payment_yields_its_order_number():
    payload, header = _signed(
        _event(
            "payment_intent.payment_failed",
            {"id": "pi_live_9", "metadata": {"order_number": "MM-20260804-002"}},
        )
    )
    parsed = sp.provider.handle_webhook(payload, header)
    assert parsed["event_type"] == "payment_intent.payment_failed"
    assert parsed["order_number"] == "MM-20260804-002"


def test_a_refund_is_read_off_the_charge():
    payload, header = _signed(
        _event(
            "charge.refunded",
            {
                "id": "ch_1",
                "payment_intent": "pi_live_7",
                "metadata": {"order_number": "MM-20260804-003"},
            },
        )
    )
    parsed = sp.provider.handle_webhook(payload, header)
    assert parsed["payment_intent_id"] == "pi_live_7"
    assert parsed["order_number"] == "MM-20260804-003"


def test_a_dispute_names_only_the_payment_intent():
    """Disputes carry no metadata of ours; the intent is the only way back."""
    payload, header = _signed(
        _event("charge.dispute.created", {"id": "dp_1", "payment_intent": "pi_live_8"})
    )
    parsed = sp.provider.handle_webhook(payload, header)
    assert parsed["payment_intent_id"] == "pi_live_8"
    assert parsed["order_number"] is None


def test_an_event_without_metadata_does_not_explode():
    """
    A payment intent created outside our checkout has no `order_number`. That is
    a "not ours, ignore it", not a crash — and a crash here is what took the
    whole endpoint down.
    """
    payload, header = _signed(_event("payment_intent.succeeded", {"id": "pi_x"}))
    parsed = sp.provider.handle_webhook(payload, header)
    assert parsed["order_number"] is None
    assert parsed["payment_intent_id"] == "pi_x"


# ── the signature is still the gate ───────────────────────────────────────────


def test_a_forged_payload_is_refused():
    """
    Reading the JSON ourselves must not mean trusting it. `construct_event` is
    still what proves the body came from Stripe; only the field extraction moved.
    """
    payload = json.dumps(
        _event("payment_intent.succeeded", {"id": "pi_forged"})
    ).encode()
    with pytest.raises(BadRequestError, match="signature"):
        sp.provider.handle_webhook(payload, "t=1,v1=deadbeef")


def test_an_unconfigured_secret_refuses_everything():
    """Open by default here would mean anyone could confirm any order."""
    payload, header = _signed(_event("payment_intent.succeeded", {"id": "pi_1"}))
    settings.STRIPE_WEBHOOK_SECRET = ""
    try:
        with pytest.raises(BadRequestError):
            sp.provider.handle_webhook(payload, header)
    finally:
        settings.STRIPE_WEBHOOK_SECRET = SECRET
