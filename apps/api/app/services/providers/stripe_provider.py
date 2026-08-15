from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Mapping

import stripe
from stripe._error import APIConnectionError, SignatureVerificationError, StripeError

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

_PAYMENT_INTENT_PREFIX = "pi_"

#: Stripe refuses an AED charge under 2.00 at its own edge.
_AED_MINIMUM = Decimal("2.00")

#: Stripe's own status codes that mean "us, not you". A 5xx or a dropped
#: connection is a reason to try the other processor; a 402 declined card is
#: emphatically not, and neither is a 400 about a malformed line item.
_UNAVAILABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def _fee_label(delivery_fee: Decimal, low_order_fee: Decimal) -> str:
    """
    What to call the combined fee line, given what is actually in it.

    The line is one line for the reasons given at the call site, but it must not
    lie about what it is. Sharjah delivers free and still charges the
    small-basket fee, so a fixed "Delivery Fee" would put `Delivery Fee AED
    15.00` on the payment page of an order whose delivery is free — the one
    place a customer is looking hardest at what they are about to be charged,
    and a discrepancy they will read as an error and abandon over.
    """
    if delivery_fee > 0 and low_order_fee > 0:
        return "Delivery & small order fee"
    if low_order_fee > 0:
        return "Small order fee"
    return "Delivery Fee"


class StripeProvider(PaymentGatewayProvider):
    """Stripe Checkout Sessions + webhook handler."""

    code = "stripe"

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _configure() -> None:
        stripe.api_key = settings.STRIPE_SECRET_KEY

    # ── PaymentGatewayProvider interface ──────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY)

    def minimum_amount(self) -> Decimal | None:
        return _AED_MINIMUM

    async def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        """
        Create a Stripe Checkout Session for the given order.

        `test_mode` is ignored: Stripe decides live-versus-test from the secret
        key itself, so honouring a per-session flag here would let the admin
        appear to switch an environment it has no way of switching.

        Async in signature only. `stripe-python`'s sync client blocks the event
        loop for the length of the call, which it has always done here — this
        change neither improves that nor makes it worse. Moving Stripe onto the
        SDK's `*_async` methods is a change against the one gateway currently
        taking live money, and does not belong in the same commit as the gateway
        it is being made interchangeable with.
        """
        self._configure()

        line_items = [
            {
                "price_data": {
                    "currency": "aed",
                    "unit_amount": int(item.unit_price * 100),
                    "product_data": {
                        "name": item.product_name,
                        "description": item.product_sku or item.product_name,
                    },
                },
                "quantity": item.quantity,
            }
            for item in order.items
        ]

        # Every fee on the order, as one line.
        #
        # They are separate columns and separate lines on the checkout and the
        # confirmation email, because a customer asking "why is there an extra
        # fifteen dirhams" deserves an answer. Stripe is not that surface — it is
        # a payment page the customer passes through, and splitting the charge
        # there buys nothing while adding a second place the total can drift
        # from `order.total`.
        #
        # Summed rather than listed, and the sum is what must equal the order:
        # a fee that exists on the order and not here charges the card less than
        # the order says, and nothing notices, because both totals are
        # internally consistent. `test_stripe_line_items_add_up_to_the_order_total`
        # is what holds this to it.
        delivery_fee = order.delivery_fee or Decimal("0")
        low_order_fee = order.low_order_fee or Decimal("0")
        fees = delivery_fee + low_order_fee
        if fees > 0:
            line_items.append(
                {
                    "price_data": {
                        "currency": "aed",
                        "unit_amount": int(fees * 100),
                        "product_data": {
                            "name": _fee_label(delivery_fee, low_order_fee)
                        },
                    },
                    "quantity": 1,
                }
            )

        # Apply discount as a Stripe coupon
        discounts = []
        if order.discount_amount and order.discount_amount > 0:
            try:
                coupon = stripe.Coupon.create(
                    amount_off=int(order.discount_amount * 100),
                    currency="aed",
                    duration="once",
                    name=f"Promo: {order.promo_code_used or 'discount'}",
                )
                discounts = [{"coupon": coupon.id}]
            except StripeError as e:
                logger.warning("Could not create Stripe coupon: %s", e)

        # The email rides along so the confirmation page can prove ownership to
        # GET /orders/{order_number} even if the guest session cookie was lost.
        success_url = checkout_urls.success_url(
            order, reference_token="{CHECKOUT_SESSION_ID}"
        )
        cancel_url = checkout_urls.cancel_url(order)

        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=line_items,
                mode="payment",
                success_url=success_url,
                cancel_url=cancel_url,
                customer_email=order.email,
                metadata={
                    "order_number": order.order_number,
                    "order_id": str(order.id),
                },
                discounts=discounts,
                payment_intent_data={
                    "metadata": {
                        "order_number": order.order_number,
                        "order_id": str(order.id),
                    },
                },
                idempotency_key=f"sess_{order.order_number}",
            )
        except APIConnectionError as e:
            # Never reached Stripe at all. The order is fine; the road is not.
            logger.error("Stripe unreachable creating session: %s", e)
            raise GatewayUnavailableError(f"Stripe unreachable: {e}") from e
        except StripeError as e:
            status = getattr(e, "http_status", None)
            if status in _UNAVAILABLE_STATUSES:
                logger.error("Stripe returned %s creating session: %s", status, e)
                raise GatewayUnavailableError(f"Stripe error {status}: {e}") from e
            logger.error("Stripe session creation failed: %s", e)
            raise BadRequestError(
                f"Payment session creation failed: {getattr(e, 'user_message', None) or str(e)}"
            )

        return GatewaySession(
            session_id=session.id,
            checkout_url=session.url,
            # Stripe's Payment Intent does not exist until the customer pays, so
            # there is deliberately nothing to record here yet. The confirmation
            # webhook is what fills it in.
            payment_id=None,
            raw_status=getattr(session, "status", None),
        )

    async def refund(
        self,
        *,
        payment_id: str,
        amount: Decimal,
        idempotency_key: str,
        test_mode: bool = False,
    ) -> GatewayRefund:
        """
        Refund part or all of a Payment Intent.

        `test_mode` is ignored for the same reason `create_session` ignores it:
        the secret key decides live-versus-test, and a per-call flag would only
        appear to switch an environment it cannot switch.

        The idempotency key is Stripe's own header, so a second call with the
        same key returns the *first* refund rather than making another. That
        matters more here than anywhere else in this file — the caller is a
        status transition reachable from an admin click, a webhook and a retry
        sweep, and without this the third of those pays the customer twice.

        `pending` is a success. Card refunds settle over days, and treating
        anything but `succeeded` as failure would have the shop refund again.
        """
        self._configure()
        try:
            refund = stripe.Refund.create(
                payment_intent=payment_id,
                amount=int(amount * 100),
                idempotency_key=idempotency_key,
            )
        except APIConnectionError as exc:
            # The processor, not the request. The caller may try again — and
            # will, with the same key, so a refund that actually went through
            # before the connection dropped is not duplicated.
            raise GatewayUnavailableError(f"Stripe was unreachable: {exc}") from exc
        except StripeError as exc:
            # Everything else is the request: already refunded, more than was
            # captured, a payment intent that never succeeded. Retrying does not
            # fix any of them.
            raise BadRequestError(f"Stripe refused the refund: {exc}") from exc

        raw_status = str(refund.get("status") or "")
        return GatewayRefund(
            refund_id=str(refund.get("id") or ""),
            # Stripe answers in minor units, and answers with what it actually
            # did rather than what was asked for.
            amount=Decimal(refund.get("amount") or 0) / 100,
            status=_REFUND_STATUSES.get(raw_status, "pending"),
            raw_status=raw_status,
        )

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        """
        Verify a Stripe webhook and translate it into a `GatewayEvent`.

        Handles, and maps:
          - payment_intent.succeeded      → SUCCEEDED
          - payment_intent.payment_failed → FAILED
          - payment_intent.canceled       → CANCELLED
          - charge.refunded               → REFUNDED
          - charge.dispute.created        → DISPUTED
        Everything else → UNHANDLED, which is acknowledged and applied to nothing.
        """
        signature = _header(headers, "stripe-signature")
        if not signature:
            raise BadRequestError("Missing Stripe-Signature header")

        if not settings.STRIPE_WEBHOOK_SECRET:
            raise BadRequestError("Stripe webhook secret not configured")

        try:
            stripe.Webhook.construct_event(
                payload, signature, settings.STRIPE_WEBHOOK_SECRET
            )
        except SignatureVerificationError as e:
            logger.warning("Stripe webhook signature verification failed: %s", e)
            raise BadRequestError("Invalid webhook signature")
        except Exception as e:
            logger.error("Stripe webhook parsing error: %s", e)
            raise BadRequestError("Could not parse webhook payload")

        # Verified above; read below. The fields are taken out of the raw JSON
        # rather than off the SDK's object model, deliberately.
        #
        # `construct_event` returns typed resources — `event["data"]["object"]`
        # is a `PaymentIntent`, not a dict — and since stripe-python 8 those no
        # longer subclass dict, so `.get()` raises `AttributeError: get`. That is
        # exactly what happened: every `payment_intent.succeeded` since the SDK
        # was upgraded threw there, and orders that were genuinely paid sat in
        # `created`. The SDK's job is to prove the payload is authentic; JSON we
        # already trust is a shape that cannot be changed out from under us by a
        # minor version bump.
        try:
            body = json.loads(payload)
        except ValueError:
            raise BadRequestError("Could not parse webhook payload")

        raw_type: str = body.get("type", "")
        obj: dict = (body.get("data") or {}).get("object") or {}
        metadata: dict = obj.get("metadata") or {}

        order_number: str | None = None
        payment_id: str | None = None
        session_id: str | None = None
        amount_refunded: int | None = None
        amount_captured: int | None = None
        fully_refunded: bool | None = None
        error_code: str | None = None
        error_message: str | None = None

        if raw_type.startswith("payment_intent."):
            payment_id = obj.get("id")
            order_number = metadata.get("order_number")
            last_error = obj.get("last_payment_error") or {}
            error_code = last_error.get("code")
            error_message = last_error.get("message")
        elif raw_type == "charge.dispute.created":
            payment_id = obj.get("payment_intent")
        elif raw_type.startswith("charge."):
            payment_id = obj.get("payment_intent")
            order_number = metadata.get("order_number")
            # A refund is not automatically the whole order. `refunded` is
            # Stripe's own "nothing left on this charge" flag; the amounts are
            # carried too so a partial can be reported accurately in minor units.
            amount_refunded = obj.get("amount_refunded")
            amount_captured = obj.get("amount")
            fully_refunded = obj.get("refunded")
        elif raw_type.startswith("checkout.session."):
            session_id = obj.get("id")
            payment_id = obj.get("payment_intent")
            order_number = metadata.get("order_number")

        logger.info(
            "Stripe webhook: type=%s order=%s payment_intent=%s",
            raw_type,
            order_number,
            payment_id,
        )

        return GatewayEvent(
            event_id=body.get("id"),
            event_type=_EVENT_TYPES.get(raw_type, PaymentEventType.UNHANDLED),
            raw_type=raw_type,
            order_number=order_number,
            session_id=session_id,
            payment_id=payment_id,
            amount_refunded=amount_refunded,
            amount_captured=amount_captured,
            fully_refunded=fully_refunded,
            error_code=error_code,
            error_message=error_message,
        )

    def is_confirmed_payment_id(self, payment_id: str | None) -> bool:
        """
        True when *payment_id* is a confirmed Payment Intent (not a session).

        Kept only for orders written before `payment_transactions` existed,
        which have no row to read and whose paid-ness is recoverable from
        nothing but this prefix. New code asks the transaction; see
        `payment_service._is_paid`.
        """
        return bool(payment_id and payment_id.startswith(_PAYMENT_INTENT_PREFIX))


#: Stripe's vocabulary → ours. A dict rather than a chain of `if`s because it is
#: a translation table, and the thing a reader wants from it is the whole
#: mapping at once.
_EVENT_TYPES: dict[str, PaymentEventType] = {
    "payment_intent.succeeded": PaymentEventType.SUCCEEDED,
    "payment_intent.payment_failed": PaymentEventType.FAILED,
    "payment_intent.canceled": PaymentEventType.CANCELLED,
    "charge.refunded": PaymentEventType.REFUNDED,
    "charge.dispute.created": PaymentEventType.DISPUTED,
}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """
    Case-insensitively read a header out of whatever mapping we were handed.

    Starlette's own `request.headers` is already case-insensitive; a plain dict
    built in a test is not, and a signature check that passes in production and
    fails in a test for that reason is a bad afternoon.
    """
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


#: Stripe's refund statuses onto the three this application uses, which are
#: Ziina's — chosen because they are the smaller, plainer set and because a
#: refund only ever needs to answer "did it work, is it still going, or did it
#: fail". `requires_action` is rare (a few bank rails) and is a pending refund
#: from the shop's point of view: nobody here can take the action.
_REFUND_STATUSES = {
    "succeeded": "completed",
    "pending": "pending",
    "requires_action": "pending",
    "failed": "failed",
    "canceled": "failed",
}


# Module-level singleton — imported by the gateway registry
provider = StripeProvider()
