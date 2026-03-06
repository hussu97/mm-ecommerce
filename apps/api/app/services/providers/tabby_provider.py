"""
Tabby payment provider stub.

Tabby is a BNPL (Buy Now Pay Later) service popular in the UAE/KSA.
Full integration requires a Tabby merchant account and sandbox credentials.

TODO:
  1. Install httpx (already in pyproject.toml) for async API calls
  2. Create a Tabby checkout session via POST https://api.tabby.ai/api/v2/checkout
     - Headers: Authorization: Bearer {TABBY_SECRET_KEY}
     - Body: { payment: { amount, currency, description, buyer, order, ... } }
  3. Return session.configuration.available_products.installments[0].web_url as checkout_url
  4. Handle webhooks at POST /payments/webhooks/tabby:
     - Verify Tabby signature (HMAC-SHA256 with TABBY_PUBLIC_KEY)
     - Event type "payment.closed" → payment confirmed
  5. Docs: https://docs.tabby.ai/
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.models.order import Order


def create_session(order: Order) -> dict:
    """TODO: Implement Tabby BNPL checkout session creation."""
    raise BadRequestError("Tabby payment provider is not yet integrated. Coming soon.")


def handle_webhook(payload: bytes, signature: str) -> dict:
    """TODO: Implement Tabby webhook verification and event handling."""
    raise BadRequestError("Tabby webhooks not yet implemented.")
