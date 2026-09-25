"""Request and response shapes for the payment routes that are new enough to
live here rather than beside their router (CLAUDE.md rule 11). The older ones in
`app/api/v1/payments.py` move over as that file is touched."""

from __future__ import annotations

from pydantic import BaseModel

__all__ = ["PaymobApplePaySessionRequest", "PaymobApplePaySessionResponse"]


class PaymobApplePaySessionRequest(BaseModel):
    order_number: str


class PaymobApplePaySessionResponse(BaseModel):
    """What the browser mounts Paymob's Pixel Apple Pay button with."""

    #: Paymob's publishable key — safe in a browser, and needed there: Pixel is
    #: initialised with it alongside the intention's client secret.
    public_key: str
    client_secret: str
    #: The server's figure, so the sheet shows what the card is charged.
    amount: str
    currency: str
    order_number: str
