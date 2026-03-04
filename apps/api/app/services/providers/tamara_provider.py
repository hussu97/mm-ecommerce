"""
Tamara payment provider stub.

Tamara is a BNPL service operating in UAE, KSA, and Kuwait.
Full integration requires a Tamara merchant account and sandbox token.

TODO:
  1. Create Tamara order session via POST {TAMARA_API_URL}/checkout
     - Headers: Authorization: Bearer {TAMARA_API_KEY}
     - Body: { order_reference_id, total_amount, currency, items, consumer, ... }
  2. Return response.checkout_url as the redirect URL
  3. Handle webhooks at POST /payments/webhooks/tamara:
     - Verify signature from X-Tamara-Signature header
     - Event "order_approved" → payment confirmed
  4. Capture payment after approval: POST {TAMARA_API_URL}/orders/{order_id}/payments/simplified-capture
  5. Docs: https://docs.tamara.co/
"""
from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.models.order import Order


def create_session(order: Order) -> dict:
    """TODO: Implement Tamara BNPL checkout session creation."""
    raise BadRequestError("Tamara payment provider is not yet integrated. Coming soon.")


def handle_webhook(payload: bytes, signature: str) -> dict:
    """TODO: Implement Tamara webhook verification and event handling."""
    raise BadRequestError("Tamara webhooks not yet implemented.")
