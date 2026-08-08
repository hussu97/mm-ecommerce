"""
Reading Ziina, whose API is missing three things Stripe's has.

Each test here corresponds to one of them, because each is a place a naive port
of the Stripe provider would have been silently wrong rather than loudly broken:

* no metadata, so the order is found from the handle instead;
* no separate confirmed-payment ID, so state must come from `status`;
* no event IDs, so dedup needs a synthetic key that survives a retry and does
  *not* collapse two different transitions into one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.services.providers import ziina_provider as zp
from app.services.providers.base import GatewayUnavailableError, PaymentEventType

SECRET = "ziina_test_secret"


def _signed(body: dict) -> tuple[bytes, dict[str, str]]:
    """A payload and the header Ziina would send with it."""
    payload = json.dumps(body).encode()
    signature = hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return payload, {"X-Hmac-Signature": signature}


def _intent_event(status: str, intent_id: str = "pi_ziina_1", **extra) -> dict:
    return {
        "event": "payment_intent.status.updated",
        "data": {"id": intent_id, "status": status, "amount": 12500, **extra},
    }


@pytest.fixture(autouse=True)
def webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "ZIINA_WEBHOOK_SECRET", SECRET)


# ── status is the state, not the ID prefix ────────────────────────────────────


class TestStatusMapping:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("completed", PaymentEventType.SUCCEEDED),
            ("failed", PaymentEventType.FAILED),
            ("canceled", PaymentEventType.CANCELLED),
        ],
    )
    def test_terminal_statuses_map_to_ours(self, status, expected):
        payload, headers = _signed(_intent_event(status))

        event = zp.provider.parse_webhook(payload, headers)

        assert event.event_type is expected
        assert event.payment_id == "pi_ziina_1"

    @pytest.mark.parametrize(
        "status",
        ["requires_payment_instrument", "requires_user_action", "pending"],
    )
    def test_a_payment_in_progress_is_applied_to_nothing(self, status):
        """
        The one that would have been a real bug. Ziina mints the intent up front
        and pushes a status for every transition, so treating "the intent
        exists" as "the money moved" would confirm an order the moment the
        customer was redirected — before they had typed a card number.
        """
        payload, headers = _signed(_intent_event(status))

        event = zp.provider.parse_webhook(payload, headers)

        assert event.event_type is PaymentEventType.UNHANDLED

    def test_a_completed_refund_is_a_refund(self):
        payload, headers = _signed(
            {
                "event": "refund.status.updated",
                "data": {
                    "id": "rf_1",
                    "payment_intent_id": "pi_ziina_1",
                    "status": "completed",
                    "amount": 12500,
                },
            }
        )

        event = zp.provider.parse_webhook(payload, headers)

        assert event.event_type is PaymentEventType.REFUNDED
        assert event.payment_id == "pi_ziina_1"
        assert event.amount_refunded == 12500

    def test_a_pending_refund_moves_nothing(self):
        """Telling a customer their money is on its way before it is is worse
        than telling them late."""
        payload, headers = _signed(
            {
                "event": "refund.status.updated",
                "data": {
                    "id": "rf_1",
                    "payment_intent_id": "pi_ziina_1",
                    "status": "pending",
                },
            }
        )

        assert (
            zp.provider.parse_webhook(payload, headers).event_type
            is PaymentEventType.UNHANDLED
        )


# ── the synthetic event id ────────────────────────────────────────────────────


class TestDedupKey:
    def test_a_redelivery_of_the_same_transition_collides(self):
        """
        Ziina retries three times on a non-2xx. Without a stable key the retry
        of a success sends the customer a second confirmation email.
        """
        first = zp.provider.parse_webhook(*_signed(_intent_event("completed")))
        again = zp.provider.parse_webhook(*_signed(_intent_event("completed")))

        assert first.event_id == again.event_id

    def test_two_different_transitions_do_not_collide(self):
        """
        The failure this guards against is subtler than a double-send: a key
        built from the intent alone would make `pending` and `completed` the
        same event, the `pending` would win the race, and the order would never
        be confirmed at all.
        """
        pending = zp.provider.parse_webhook(*_signed(_intent_event("pending")))
        done = zp.provider.parse_webhook(*_signed(_intent_event("completed")))

        assert pending.event_id != done.event_id

    def test_two_orders_do_not_collide(self):
        one = zp.provider.parse_webhook(*_signed(_intent_event("completed", "pi_a")))
        two = zp.provider.parse_webhook(*_signed(_intent_event("completed", "pi_b")))

        assert one.event_id != two.event_id


# ── the signature is the gate ─────────────────────────────────────────────────


class TestSignature:
    def test_a_forged_body_is_refused(self):
        payload, headers = _signed(_intent_event("completed"))

        tampered = payload.replace(b"12500", b"99999")

        with pytest.raises(BadRequestError, match="signature"):
            zp.provider.parse_webhook(tampered, headers)

    def test_a_missing_header_is_refused(self):
        payload, _ = _signed(_intent_event("completed"))

        with pytest.raises(BadRequestError, match="X-Hmac-Signature"):
            zp.provider.parse_webhook(payload, {})

    def test_an_unconfigured_secret_refuses_everything(self, monkeypatch):
        """
        Open by default would mean anyone who can guess a payment intent ID gets
        free cake. Unlike the courier webhooks — left open because a dropped
        status update permanently loses tracking — a dropped payment event here
        is recoverable: Ziina retries, and the intent can be re-read.
        """
        payload, headers = _signed(_intent_event("completed"))
        monkeypatch.setattr(settings, "ZIINA_WEBHOOK_SECRET", "")

        with pytest.raises(BadRequestError, match="not configured"):
            zp.provider.parse_webhook(payload, headers)

    def test_the_header_is_read_case_insensitively(self):
        """Starlette lower-cases; a dict built in a test does not."""
        payload, headers = _signed(_intent_event("completed"))
        lowered = {"x-hmac-signature": next(iter(headers.values()))}

        assert zp.provider.parse_webhook(payload, lowered).event_type is (
            PaymentEventType.SUCCEEDED
        )


# ── creating a session ────────────────────────────────────────────────────────


class TestCreateSession:
    @pytest.fixture(autouse=True)
    def configured(self, monkeypatch):
        monkeypatch.setattr(settings, "ZIINA_API_KEY", "zk_test")
        monkeypatch.setattr(settings, "ZIINA_ENABLED", True)
        monkeypatch.setattr(settings, "ZIINA_TEST_MODE", False)
        monkeypatch.setattr(settings, "WEB_URL", "https://shop.example")

    @staticmethod
    def _order():
        return SimpleNamespace(
            order_number="MM-20260808-001",
            email="customer@example.com",
            total=Decimal("125.00"),
        )

    @staticmethod
    def _client(monkeypatch, *, response=None, raises=None, captured=None):
        """
        Stand in for `httpx.AsyncClient`.

        The provider must not block the event loop — every other outbound
        integration here is already async, and a sync call in a request handler
        stalls the whole worker for the length of the timeout, serving nothing
        at all. That is worst exactly when it matters: a gateway having a bad
        day is a gateway answering slowly, and the failover is meant to be what
        saves the checkout rather than a second thing to wait on.

        So the fake is deliberately async-only. A provider that regressed to
        `httpx.post` would not call it and the tests would fail.
        """

        class _FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, **kwargs):
                if captured is not None:
                    captured.update(kwargs.get("json") or {})
                if raises is not None:
                    raise raises
                return response

        monkeypatch.setattr(zp.httpx, "AsyncClient", _FakeClient)

    async def test_the_amount_goes_across_in_fils(self, monkeypatch):
        """AED 125.00 is 12500, not 125. Getting this wrong charges a hundredth
        of the order and nothing anywhere disagrees."""
        captured: dict = {}
        self._client(
            monkeypatch,
            captured=captured,
            response=httpx.Response(
                201,
                json={
                    "id": "pi_z1",
                    "redirect_url": "https://pay.ziina.com/pi_z1",
                    "status": "requires_payment_instrument",
                },
            ),
        )

        session = await zp.provider.create_session(self._order())

        assert captured["amount"] == 12500
        assert captured["currency_code"] == "AED"
        assert session.session_id == "pi_z1"
        assert session.checkout_url == "https://pay.ziina.com/pi_z1"

    async def test_the_order_number_rides_in_the_message(self, monkeypatch):
        """
        Ziina has nowhere to put metadata, so `message` — which the customer
        sees on the hosted page — is the only place the order number can go.
        It is what makes a "which payment was this?" conversation answerable.
        """
        captured: dict = {}
        self._client(
            monkeypatch,
            captured=captured,
            response=httpx.Response(
                201, json={"id": "pi_z1", "redirect_url": "https://x/y"}
            ),
        )

        await zp.provider.create_session(self._order())

        assert "MM-20260808-001" in captured["message"]

    async def test_the_intent_is_recorded_as_the_payment_handle(self, monkeypatch):
        """
        Unlike Stripe, whose Payment Intent does not exist until the customer
        pays. Recording it now is what lets a refund webhook that quotes only
        the intent find the order.
        """
        self._client(
            monkeypatch,
            response=httpx.Response(
                201, json={"id": "pi_z1", "redirect_url": "https://x/y"}
            ),
        )

        session = await zp.provider.create_session(self._order())

        assert session.payment_id == "pi_z1" == session.session_id

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    async def test_their_outage_is_failover_worthy(self, monkeypatch, status):
        self._client(monkeypatch, response=httpx.Response(status, text="nope"))

        with pytest.raises(GatewayUnavailableError):
            await zp.provider.create_session(self._order())

    async def test_an_unreachable_host_is_failover_worthy(self, monkeypatch):
        self._client(monkeypatch, raises=httpx.ConnectError("no route to host"))

        with pytest.raises(GatewayUnavailableError):
            await zp.provider.create_session(self._order())

    async def test_a_timeout_is_failover_worthy(self, monkeypatch):
        """
        The one that matters most in an incident: a gateway having a bad day
        answers slowly rather than refusing outright.
        """
        self._client(monkeypatch, raises=httpx.ReadTimeout("too slow"))

        with pytest.raises(GatewayUnavailableError):
            await zp.provider.create_session(self._order())

    async def test_a_refusal_is_not_failover_worthy(self, monkeypatch):
        """
        A 400 is an opinion about what we sent. Re-presenting it to a second
        processor turns one honest refusal into two, and if the second one takes
        it the order is paid through a gateway nobody chose.
        """
        self._client(
            monkeypatch,
            response=httpx.Response(400, json={"message": "amount too low"}),
        )

        with pytest.raises(BadRequestError, match="amount too low"):
            await zp.provider.create_session(self._order())

    async def test_a_created_intent_with_nowhere_to_send_anyone_is_an_outage(
        self, monkeypatch
    ):
        """A 201 without a redirect URL is not a session, whatever it says."""
        self._client(monkeypatch, response=httpx.Response(201, json={"id": "pi_z1"}))

        with pytest.raises(GatewayUnavailableError, match="redirect"):
            await zp.provider.create_session(self._order())

    async def test_test_mode_is_passed_through(self, monkeypatch):
        captured: dict = {}
        self._client(
            monkeypatch,
            captured=captured,
            response=httpx.Response(
                201, json={"id": "pi_z1", "redirect_url": "https://x/y"}
            ),
        )

        await zp.provider.create_session(self._order(), test_mode=True)

        assert captured["test"] is True
