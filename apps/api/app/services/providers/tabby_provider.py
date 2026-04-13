"""
Tabby payment provider — stub, not yet integrated.

Tabby is a BNPL (Buy Now Pay Later) service popular in the UAE/KSA.
Full integration requires a Tabby merchant account and sandbox credentials.

Integration checklist when implementing:
  1. POST https://api.tabby.ai/api/v2/checkout
     Headers: Authorization: Bearer {TABBY_SECRET_KEY}
     Body: { payment: { amount, currency, description, buyer, order, … } }
  2. Return session.configuration.available_products.installments[0].web_url as checkout_url
  3. Webhook at POST /payments/webhooks/tabby:
     - Verify Tabby HMAC-SHA256 signature (use TABBY_PUBLIC_KEY)
     - "payment.closed" event → payment confirmed
  4. Docs: https://docs.tabby.ai/
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers.base import PaymentProvider


class TabbyProvider(PaymentProvider):
    """Stub — raises BadRequestError until implemented."""

    def create_session(self, order: Order) -> dict:
        raise BadRequestError("Tabby payment provider is not yet integrated.")

    def handle_webhook(self, payload: bytes, signature: str) -> dict:
        raise BadRequestError("Tabby webhooks are not yet implemented.")


provider = TabbyProvider()
