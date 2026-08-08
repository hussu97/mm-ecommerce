"""
Where a gateway sends the customer when it is finished with them.

One module because these three URLs are a property of *our* checkout, not of any
processor: the confirmation page, the way back to the payment step, and the
email that rides along so the confirmation can prove ownership when a guest
session cookie has been lost. Two gateways building them separately is two
places for the query string to drift, and the failure is invisible until a
customer lands on a confirmation page that cannot load their order.

What is genuinely per-gateway is only the token each one substitutes its own
handle into, so that is the one thing left to the caller.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core.config import settings
from app.models.order import Order

__all__ = ["cancel_url", "failure_url", "success_url"]


def success_url(order: Order, *, reference_token: str | None = None) -> str:
    """
    The confirmation page for *order*.

    `reference_token` is the gateway's own placeholder — Stripe's
    `{CHECKOUT_SESSION_ID}`, Ziina's `{PAYMENT_INTENT_ID}` — appended as
    `session_id` so the confirmation page has a handle regardless of which
    processor the customer went through. Omitted entirely for a gateway that
    substitutes nothing, rather than left as a literal brace in a URL bar.
    """
    url = (
        f"{settings.WEB_URL}/checkout/confirmation"
        f"?order_number={order.order_number}&email={quote(order.email)}"
    )
    if reference_token:
        url += f"&session_id={reference_token}"
    return url


def cancel_url(order: Order) -> str:
    """Back to the payment step, with the order still there to retry against."""
    return f"{settings.WEB_URL}/checkout?step=payment&order_number={order.order_number}"


def failure_url(order: Order) -> str:
    """
    Where a *failed* payment lands, as opposed to an abandoned one.

    The same place, deliberately. Both leave the customer holding an unpaid
    order and wanting to try again, and the distinction between "your card was
    declined" and "you pressed back" is one the checkout already draws from the
    order's own status rather than from which URL they arrived on.
    """
    return cancel_url(order)
