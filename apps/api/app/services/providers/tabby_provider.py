"""
Tabby payment provider — stub, not yet integrated.

Tabby is a BNPL (Buy Now Pay Later) service popular in the UAE/KSA. It is not a
card gateway and would not sit in the `payment_gateways` routing table: BNPL is
a *method* the customer chooses, alongside card and cash, not a processor we can
silently move card traffic onto. Whoever integrates it should add it to
`PaymentMethodEnum` and give it its own branch in `payment_service.create_session`
rather than a row next to Stripe and Ziina.

Full integration requires a Tabby merchant account and sandbox credentials.

Integration checklist when implementing:
  1. POST https://api.tabby.ai/api/v2/checkout
     Headers: Authorization: Bearer {TABBY_SECRET_KEY}
     Body: { payment: { amount, currency, description, buyer, order, … } }
  2. Return session.configuration.available_products.installments[0].web_url
     as `GatewaySession.checkout_url`
  3. Webhook at POST /payments/webhooks/tabby:
     - Verify Tabby HMAC-SHA256 signature (use TABBY_PUBLIC_KEY)
     - "payment.closed" event → PaymentEventType.SUCCEEDED
  4. Docs: https://docs.tabby.ai/
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


class TabbyProvider(PaymentGatewayProvider):
    """Stub — raises BadRequestError until implemented."""

    code = "tabby"

    def is_configured(self) -> bool:
        return False

    async def create_session(
        self, order: Order, *, test_mode: bool = False
    ) -> GatewaySession:
        raise BadRequestError("Tabby payment provider is not yet integrated.")

    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        raise BadRequestError("Tabby webhooks are not yet implemented.")


provider = TabbyProvider()
