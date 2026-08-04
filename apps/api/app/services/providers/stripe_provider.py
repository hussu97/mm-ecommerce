from __future__ import annotations

import json
import logging
from urllib.parse import quote

import stripe
from stripe._error import SignatureVerificationError, StripeError

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers.base import PaymentProvider

logger = logging.getLogger(__name__)

_PAYMENT_INTENT_PREFIX = "pi_"


class StripeProvider(PaymentProvider):
    """Stripe Checkout Sessions + webhook handler."""

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _configure() -> None:
        stripe.api_key = settings.STRIPE_SECRET_KEY

    # ── PaymentProvider interface ─────────────────────────────────────────────

    def create_session(self, order: Order) -> dict:
        """
        Create a Stripe Checkout Session for the given order.
        Returns {session_id, checkout_url}.
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

        if order.delivery_fee and order.delivery_fee > 0:
            line_items.append(
                {
                    "price_data": {
                        "currency": "aed",
                        "unit_amount": int(order.delivery_fee * 100),
                        "product_data": {"name": "Delivery Fee"},
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
        success_url = (
            f"{settings.WEB_URL}/checkout/confirmation"
            f"?order_number={order.order_number}&email={quote(order.email)}"
            f"&session_id={{CHECKOUT_SESSION_ID}}"
        )
        cancel_url = f"{settings.WEB_URL}/checkout?step=payment&order_number={order.order_number}"

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
        except StripeError as e:
            logger.error("Stripe session creation failed: %s", e)
            raise BadRequestError(
                f"Payment session creation failed: {getattr(e, 'user_message', None) or str(e)}"
            )

        return {"session_id": session.id, "checkout_url": session.url}

    def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """
        Verify and parse a Stripe webhook event.

        Returns a normalised dict with:
          event_id, event_type, order_number, payment_intent_id
        """
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

        event_type: str = body.get("type", "")
        obj: dict = (body.get("data") or {}).get("object") or {}
        metadata: dict = obj.get("metadata") or {}

        order_number: str | None = None
        payment_intent_id: str | None = None

        if event_type.startswith("payment_intent."):
            payment_intent_id = obj.get("id")
            order_number = metadata.get("order_number")
        elif event_type == "charge.dispute.created":
            payment_intent_id = obj.get("payment_intent")
        elif event_type.startswith("charge."):
            payment_intent_id = obj.get("payment_intent")
            order_number = metadata.get("order_number")

        logger.info(
            "Stripe webhook: type=%s order=%s payment_intent=%s",
            event_type,
            order_number,
            payment_intent_id,
        )

        return {
            "event_id": body.get("id"),
            "event_type": event_type,
            "order_number": order_number,
            "payment_intent_id": payment_intent_id,
        }

    def is_confirmed_payment_id(self, payment_id: str | None) -> bool:
        """True when payment_id is a confirmed Payment Intent (not a pending session)."""
        return bool(payment_id and payment_id.startswith(_PAYMENT_INTENT_PREFIX))


# Module-level singleton — imported by payment_service
provider = StripeProvider()
