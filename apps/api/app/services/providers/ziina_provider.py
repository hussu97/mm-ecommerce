"""
Ziina Payment Intents — the second card gateway.

Ziina is a UAE processor, and the reason it is here is redundancy: when Stripe
has an incident, "switch the card estate to the other one" should be a row in a
table, not a deploy.

Three things about their API shape the code below, and each one is a place a
naive port of the Stripe provider would have been quietly wrong.

**There is no metadata.** `CreatePaymentIntentDto` has nine fields and none of
them will hold an order number. Stripe hands its own metadata back on every
webhook, so the order was always findable from the event alone; Ziina hands back
a payment intent and nothing else. Correlation therefore has to run the other
way — through the handle we stored when we created the intent — which is exactly
what `payment_transactions` is for. The order number is *also* put in `message`,
which is displayed to the customer on the hosted page, so a support conversation
and a bank statement line both name the order.

**The payment intent exists before the payment does.** Stripe's Checkout Session
(`cs_…`) becomes a Payment Intent (`pi_…`) only when the customer pays, which is
why `payment_id.startswith("pi_")` used to be a workable stand-in for "paid".
Ziina mints the intent up front. The same string is present on an order nobody
has paid for, so state has to come from `status`, never from the ID.

**There are no event IDs.** The webhook body is `{event, data}` and nothing in
it is unique per delivery. Ziina retries three times on a non-2xx, so without a
synthetic key a retried success would be applied twice — and "applied twice"
here means a second confirmation email and a second owner notification for an
order that was already confirmed. The key is built from the transition itself:
gateway, event name, intent, status. Redeliveries of the same transition collide
and dedup; a genuine later transition does not.

Amounts are in fils. `10000` is AED 100.00.

Docs: https://docs.ziina.com/api-reference
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from decimal import Decimal
from typing import Any, Mapping

import httpx

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers import checkout_urls
from app.services.providers.base import (
    GatewayEvent,
    GatewayRefund,
    GatewaySession,
    GatewayUnavailableError,
    PaymentEventType,
    PaymentGatewayProvider,
)

logger = logging.getLogger(__name__)

__all__ = ["ZiinaProvider", "provider"]

#: Ziina refuses an AED charge under 2.00, same as Stripe.
_AED_MINIMUM = Decimal("2.00")

#: The placeholder Ziina substitutes the intent's own ID into on the way back,
#: so the confirmation page gets a handle without us having to guess one.
_INTENT_PLACEHOLDER = "{PAYMENT_INTENT_ID}"

_SIGNATURE_HEADER = "x-hmac-signature"

#: Answers that mean "try again, or try someone else" rather than "your order is
#: wrong". A 4xx from Ziina is about what we sent; these are about Ziina.
_UNAVAILABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Ziina's payment-intent statuses → ours.
#:
#: The three absent from this map — `requires_payment_instrument`,
#: `requires_user_action`, `pending` — are the middle of a payment in progress.
#: They are real transitions and they are journalled, and acting on any of them
#: would confirm an order against money that has not moved.
_INTENT_STATUSES: dict[str, PaymentEventType] = {
    "completed": PaymentEventType.SUCCEEDED,
    "failed": PaymentEventType.FAILED,
    "canceled": PaymentEventType.CANCELLED,
}

#: Refund statuses → ours. A refund that is still `pending` has not returned any
#: money yet, and telling the customer it has is worse than telling them late.
_REFUND_STATUSES: dict[str, PaymentEventType] = {
    "completed": PaymentEventType.REFUNDED,
}


class ZiinaProvider(PaymentGatewayProvider):
    """Ziina hosted payment pages + webhook handler."""

    code = "ziina"

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _base_url() -> str:
        return settings.ZIINA_API_URL.rstrip("/")

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.ZIINA_API_KEY}",
            "Content-Type": "application/json",
        }

    # ── PaymentGatewayProvider interface ──────────────────────────────────────

    def is_configured(self) -> bool:
        """
        Whether this environment may charge a card on Ziina.

        `ZIINA_ENABLED` is a separate switch from the credentials on purpose,
        and it is the one that keeps production on Stripe. Keys can arrive on a
        VM for a hundred legitimate reasons — a shared secret store, a copied
        `.env`, a test that was run once — and none of them is a decision to
        start routing live card traffic through a processor that has not been
        signed off. The flag is that decision, written down, defaulting to no.
        """
        return bool(settings.ZIINA_ENABLED and settings.ZIINA_API_KEY)

    def minimum_amount(self) -> Decimal | None:
        return _AED_MINIMUM

    async def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        """
        Create a Ziina Payment Intent and return its hosted page.

        Unlike Stripe there are no line items — Ziina charges one amount — so
        `order.total` goes across whole. That is not a downgrade: the Stripe
        session's items exist to make its payment page legible, and its own
        invariant was always that they *sum* to `order.total`. Here the sum is
        the only thing there is, so the invariant cannot be violated.
        """
        if not settings.ZIINA_API_KEY:
            # Should be unreachable — the router checks `is_configured` first —
            # but an unconfigured gateway must not produce a bare 401 from
            # someone else's API in the middle of a checkout.
            raise BadRequestError("Ziina is not configured in this environment")

        total = Decimal(str(order.total))
        payload: dict[str, Any] = {
            "amount": int(total * 100),
            "currency_code": "AED",
            # Shown to the customer on the hosted page, and the closest thing
            # Ziina has to metadata. The order number being here is what makes a
            # "which payment was this?" conversation answerable at all.
            "message": f"Melting Moments · {order.order_number}",
            "success_url": checkout_urls.success_url(
                order, reference_token=_INTENT_PLACEHOLDER
            ),
            "cancel_url": checkout_urls.cancel_url(order),
            "failure_url": checkout_urls.failure_url(order),
            "test": bool(test_mode or settings.ZIINA_TEST_MODE),
        }

        try:
            async with httpx.AsyncClient(
                timeout=settings.ZIINA_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    f"{self._base_url()}/payment_intent",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            logger.error("Ziina unreachable creating payment intent: %s", exc)
            raise GatewayUnavailableError(f"Ziina unreachable: {exc}") from exc

        if response.status_code in _UNAVAILABLE_STATUSES:
            logger.error(
                "Ziina returned %s creating payment intent: %s",
                response.status_code,
                response.text[:500],
            )
            raise GatewayUnavailableError(
                f"Ziina error {response.status_code}: {response.text[:200]}"
            )

        if response.status_code >= 400:
            detail = _error_message(response)
            logger.error(
                "Ziina refused payment intent for %s: %s %s",
                order.order_number,
                response.status_code,
                detail,
            )
            raise BadRequestError(f"Payment session creation failed: {detail}")

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailableError(
                "Ziina returned a payment intent that was not JSON"
            ) from exc

        intent_id = body.get("id")
        redirect_url = body.get("redirect_url")
        if not intent_id or not redirect_url:
            # A 201 with nowhere to send the customer is not a session. Treated
            # as the gateway being unwell rather than the order being wrong, so
            # the router can fall back rather than failing the checkout.
            logger.error(
                "Ziina payment intent for %s had no id/redirect_url: %s",
                order.order_number,
                json.dumps(body)[:500],
            )
            raise GatewayUnavailableError(
                "Ziina returned a payment intent with no redirect URL"
            )

        logger.info(
            "Ziina payment intent created: order=%s intent=%s test=%s",
            order.order_number,
            intent_id,
            payload["test"],
        )

        return GatewaySession(
            session_id=intent_id,
            checkout_url=redirect_url,
            # Ziina's intent *is* the payment handle, from the moment it exists.
            # Recorded here so a refund or a webhook that quotes only the intent
            # can find the order without waiting for a confirmation first.
            payment_id=intent_id,
            raw_status=body.get("status"),
        )

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        """Verify `X-Hmac-Signature` and translate `{event, data}` into ours."""
        self._verify_signature(payload, headers)

        try:
            body = json.loads(payload)
        except ValueError as exc:
            raise BadRequestError("Could not parse webhook payload") from exc

        if not isinstance(body, dict):
            raise BadRequestError("Webhook body was not an object")

        raw_event: str = str(body.get("event") or "")
        data: dict = body.get("data") or {}
        if not isinstance(data, dict):
            raise BadRequestError("Webhook data was not an object")

        raw_status: str = str(data.get("status") or "")
        error = data.get("latest_error") or data.get("error") or {}
        if not isinstance(error, dict):
            error = {}

        if raw_event == "refund.status.updated":
            event_type = _REFUND_STATUSES.get(raw_status, PaymentEventType.UNHANDLED)
            payment_id = data.get("payment_intent_id")
            # Ziina's refund object carries the refunded amount but not the
            # original capture, so "was this the whole order?" cannot be
            # answered from the event. It is answered against the order's own
            # total in `payment_service`, which is the only place that knows it.
            amount_refunded = _as_int(data.get("amount"))
            reference = data.get("id") or payment_id
        else:
            event_type = _INTENT_STATUSES.get(raw_status, PaymentEventType.UNHANDLED)
            payment_id = data.get("id")
            amount_refunded = None
            reference = payment_id

        logger.info(
            "Ziina webhook: event=%s status=%s intent=%s → %s",
            raw_event,
            raw_status,
            payment_id,
            event_type.value,
        )

        return GatewayEvent(
            # Synthetic, and it has to be — see the module docstring. Every part
            # is load-bearing: drop the status and a `pending → completed`
            # sequence dedups down to one event and the order is never confirmed.
            event_id=f"ziina:{raw_event}:{reference}:{raw_status}",
            event_type=event_type,
            raw_type=f"{raw_event}:{raw_status}" if raw_status else raw_event,
            # No metadata to read. The order is found from the handle instead.
            order_number=None,
            session_id=data.get("id") if raw_event != "refund.status.updated" else None,
            payment_id=payment_id,
            amount_refunded=amount_refunded,
            error_code=error.get("code"),
            error_message=error.get("message"),
            # No normalised reason, deliberately. Ziina's `latest_error.code` is
            # an HTTP status, not a decline taxonomy, and there is nothing here
            # to bucket. `error_message` is documented as human-readable, so the
            # storefront shows it verbatim rather than a made-up category.
            failure_reason=None,
        )

    # ── signature ─────────────────────────────────────────────────────────────

    @staticmethod
    def _verify_signature(payload: bytes, headers: Mapping[str, str]) -> None:
        """
        Hex SHA-256 HMAC of the raw body, under the secret we registered.

        Refusing when no secret is configured, rather than waving the request
        through, is the deliberate choice. An unsigned webhook endpoint that
        confirms orders is an endpoint where anyone who can guess a payment
        intent ID gets free cake — and unlike the courier webhooks, which are
        left open because dropping a status update loses tracking, dropping a
        payment event here loses nothing permanently: Ziina retries, and the
        order's status can also be recovered by polling the intent.
        """
        secret = (settings.ZIINA_WEBHOOK_SECRET or "").strip()
        if not secret:
            logger.error(
                "Ziina webhook rejected — ZIINA_WEBHOOK_SECRET is not configured. "
                "Register the webhook with a secret via POST /webhook."
            )
            raise BadRequestError("Ziina webhook secret not configured")

        presented = None
        for key, value in headers.items():
            if key.lower() == _SIGNATURE_HEADER:
                presented = value
                break

        if not presented:
            raise BadRequestError("Missing X-Hmac-Signature header")

        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

        # Constant time, so a valid signature cannot be found a character at a
        # time by watching how long the comparison takes.
        if not hmac.compare_digest(presented.strip().lower(), expected):
            logger.warning("Ziina webhook signature verification failed")
            raise BadRequestError("Invalid webhook signature")

    # ── operational helpers ───────────────────────────────────────────────────

    async def refund(
        self,
        *,
        payment_id: str,
        amount: Decimal,
        idempotency_key: str,
        test_mode: bool = False,
    ) -> GatewayRefund:
        """
        Refund part or all of a Ziina payment intent.

        `POST /refund`, documented at docs.ziina.com/api-reference/refund/create.
        Needs the `write_refunds` scope on the token — a key minted only for
        charging will 403 here, which is a configuration problem and reads as
        one rather than as a gateway outage.

        **The idempotency key is the refund's own `id`.** Ziina takes a
        client-generated UUID as the primary key of the refund, which is a
        stronger guarantee than a header: the same id asked for twice is the
        same row on their side, not a second refund that happens to match. The
        caller derives it from the order and the amount, so a retry after a
        dropped connection cannot pay the customer twice.

        Amount is in minor units, matching `create_session` — Ziina's "base
        units" are fils for AED.
        """
        if not settings.ZIINA_API_KEY:
            raise BadRequestError("Ziina is not configured in this environment")

        payload: dict[str, Any] = {
            "id": idempotency_key,
            "payment_intent_id": payment_id,
            "amount": int(amount * 100),
            "currency_code": "AED",
            "test": bool(test_mode or settings.ZIINA_TEST_MODE),
        }

        try:
            async with httpx.AsyncClient(
                timeout=settings.ZIINA_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    f"{self._base_url()}/refund",
                    json=payload,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            logger.error("Ziina unreachable issuing refund: %s", exc)
            raise GatewayUnavailableError(f"Ziina unreachable: {exc}") from exc

        if response.status_code in _UNAVAILABLE_STATUSES:
            logger.error(
                "Ziina returned %s issuing a refund: %s",
                response.status_code,
                response.text[:500],
            )
            raise GatewayUnavailableError(
                f"Ziina error {response.status_code}: {response.text[:200]}"
            )

        if response.status_code >= 400:
            detail = _error_message(response)
            logger.error(
                "Ziina refused a refund for %s: %s %s",
                payment_id,
                response.status_code,
                detail,
            )
            raise BadRequestError(f"Refund failed: {detail}")

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayUnavailableError(
                "Ziina returned a refund that was not JSON"
            ) from exc

        raw_status = str(body.get("status") or "")
        if raw_status == "failed":
            # A 200 carrying a failure. Their `error` object says why, and this
            # is a refusal rather than an outage — retrying sends the same
            # request to the same answer.
            error = body.get("error") or {}
            raise BadRequestError(
                f"Ziina declined the refund: {error.get('message') or 'no reason given'}"
            )

        amount_minor = body.get("amount")
        return GatewayRefund(
            refund_id=str(body.get("id") or idempotency_key),
            amount=(
                Decimal(amount_minor) / 100 if amount_minor is not None else amount
            ),
            # Their three statuses are the ones this application uses, so there
            # is nothing to translate — `pending` included, which is a success:
            # a refund that is still moving is not a refund to issue again.
            status=raw_status or "pending",
            raw_status=raw_status or None,
        )

    async def register_webhook(self, url: str, secret: str) -> dict:
        """
        Point Ziina's webhook at *url*, signed with *secret*.

        Ziina has no dashboard for this — the URL is set by an API call, and
        "any subsequent calls to this URL will overwrite the webhook URL for
        your account". Exposed as a method rather than a script so it can be
        driven from a management command with the same credentials the rest of
        the provider uses, and so the one-account-one-URL constraint is written
        down somewhere a reader will find it before they set it twice.
        """
        async with httpx.AsyncClient(timeout=settings.ZIINA_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{self._base_url()}/webhook",
                json={"url": url, "secret": secret},
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def fetch_payment_intent(self, intent_id: str) -> dict:
        """
        Read an intent back from Ziina.

        The reconciliation path. Ziina retries a webhook three times and then
        stops, so a deploy window longer than those three attempts is a payment
        that moved with nothing recorded — and without metadata on their side,
        nobody at Ziina can tell us which order it was. This is how that gets
        answered: we hold the intent ID against the order already, so the truth
        is one GET away.
        """
        async with httpx.AsyncClient(timeout=settings.ZIINA_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{self._base_url()}/payment_intent/{intent_id}",
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()


def _error_message(response: httpx.Response) -> str:
    """Whatever Ziina said went wrong, in a form fit to show a customer."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        message = body.get("message") or (body.get("error") or {}).get("message")
        if message:
            return str(message)[:200]
    return f"HTTP {response.status_code}"


def _as_int(value: Any) -> int | None:
    """Minor units, or nothing. Never a partially-parsed number."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Module-level singleton — imported by the gateway registry
provider = ZiinaProvider()
