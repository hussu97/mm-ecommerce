"""
Tamara payment provider — stub, not yet integrated.

Tamara is a BNPL service operating in UAE, KSA, and Kuwait.
Full integration requires a Tamara merchant account and sandbox token.

Integration checklist when implementing:
  1. POST {TAMARA_API_URL}/checkout
     Headers: Authorization: Bearer {TAMARA_API_KEY}
     Body: { order_reference_id, total_amount, currency, items, consumer, … }
  2. Return response.checkout_url as the redirect URL
  3. Webhook at POST /payments/webhooks/tamara:
     - Verify X-Tamara-Signature header
     - "order_approved" event → payment confirmed
  4. Capture payment: POST {TAMARA_API_URL}/orders/{order_id}/payments/simplified-capture
  5. Docs: https://docs.tamara.co/
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers.base import PaymentProvider


class TamaraProvider(PaymentProvider):
    """Stub — raises BadRequestError until implemented."""

    def create_session(self, order: Order) -> dict:
        raise BadRequestError("Tamara payment provider is not yet integrated.")

    def handle_webhook(self, payload: bytes, signature: str) -> dict:
        raise BadRequestError("Tamara webhooks are not yet implemented.")


provider = TamaraProvider()
