"""
Tamara payment provider — stub, not yet integrated.

Tamara is a BNPL service operating in UAE, KSA, and Kuwait. Like Tabby it is a
payment *method*, not a card gateway — see the note in `tabby_provider` for why
that means it does not belong in the `payment_gateways` routing table.

Full integration requires a Tamara merchant account and sandbox token.

Integration checklist when implementing:
  1. POST {TAMARA_API_URL}/checkout
     Headers: Authorization: Bearer {TAMARA_API_KEY}
     Body: { order_reference_id, total_amount, currency, items, consumer, … }
  2. Return response.checkout_url as `GatewaySession.checkout_url`
  3. Webhook at POST /payments/webhooks/tamara:
     - Verify X-Tamara-Signature header
     - "order_approved" event → PaymentEventType.SUCCEEDED
  4. Capture payment: POST {TAMARA_API_URL}/orders/{order_id}/payments/simplified-capture
  5. Docs: https://docs.tamara.co/
"""

from __future__ import annotations

from typing import Mapping

from app.core.exceptions import BadRequestError
from app.models.order import Order
from app.services.providers.base import (
    GatewayEvent,
    GatewaySession,
    PaymentGatewayProvider,
)


class TamaraProvider(PaymentGatewayProvider):
    """Stub — raises BadRequestError until implemented."""

    code = "tamara"

    def is_configured(self) -> bool:
        return False

    def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        raise BadRequestError("Tamara payment provider is not yet integrated.")

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        raise BadRequestError("Tamara webhooks are not yet implemented.")


provider = TamaraProvider()
