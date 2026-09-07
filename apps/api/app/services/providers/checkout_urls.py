"""
Where a gateway sends the customer when it is finished with them.

One module because these three URLs are a property of *our* checkout, not of any
processor: the confirmation page, the way back to the payment step, and the
signed receipt token that rides along so the confirmation can prove ownership
when a guest session cookie has been lost. Two gateways building them separately
is two places for the query string to drift, and the failure is invisible until
a customer lands on a confirmation page that cannot load their order.

What is genuinely per-gateway is only the placeholder each one substitutes its
own handle into, so that is the one thing left to the caller.
"""

from __future__ import annotations

from urllib.parse import quote

from app.core import receipt_token
from app.core.config import settings
from app.models.order import Order

__all__ = ["cancel_url", "failure_url", "success_url"]


def success_url(order: Order, *, reference_token: str | None = None) -> str:
    """
    The confirmation page for *order*.

    Carries a signed `token` rather than the customer's email (F-WEB-2/F-ORD-20).
    The email used to ride here as the ownership credential the confirmation page
    replays to `GET /orders/{order_number}` — but a `success_url` is handed to
    Stripe and Ziina, kept in browser history, and read by the analytics beacon,
    so the address leaked into all three. A `receipt_token` proves ownership of
    this one order, cannot be read back into an email, and expires; the
    email-ownership path still works for links already in the wild.

    `reference_token` is the gateway's own placeholder — Stripe's
    `{CHECKOUT_SESSION_ID}`, Ziina's `{PAYMENT_INTENT_ID}` — appended as
    `session_id` so the confirmation page has a handle regardless of which
    processor the customer went through. Omitted entirely for a gateway that
    substitutes nothing, rather than left as a literal brace in a URL bar.
    """
    url = (
        f"{settings.WEB_URL}/checkout/confirmation"
        f"?order_number={order.order_number}"
        f"&token={quote(receipt_token.mint(order.id))}"
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
