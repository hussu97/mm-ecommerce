"""
A signed handle that proves the bearer is the customer who placed an order,
without carrying their email.

The confirmation and track pages have to prove ownership to
`GET /orders/{order_number}` and `POST /orders/track`, and the only credential
they had was the customer's own email — so the payment gateway's `success_url`
carried it in the query string, from where it landed in Stripe's and Ziina's
logs, the browser history and, worst, the analytics beacon (F-WEB-2/F-ORD-20).

This mints a short-lived HMAC token over the order's id instead. It is enough
to read the one order it names and nothing else, it cannot be turned back into
an email, and it expires — so a link that leaks is a link to one receipt for a
fortnight, not a customer's address forever.

Signed with the app's existing `SECRET_KEY` (the same HS256 key the auth JWTs
use), deliberately: an order receipt is exactly as sensitive as a login token,
the key is already provisioned in every environment, and a new secret would be
a new value to write in five deploy files and a redeploy before a single link
worked. The `type` claim keeps these tokens from being confused with access or
reset tokens signed by the same key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from .config import settings
from .security import ALGORITHM

__all__ = ["DEFAULT_TTL", "mint", "order_id_from"]

#: How long a receipt link stays good. Long enough that the confirmation email's
#: link still works when the customer opens it the next day, short enough that a
#: leaked URL is not a standing credential. A signed-in customer never needs it —
#: they own the order by account — and a guest who loses it falls back to the
#: order-number-plus-email lookup, which still works.
DEFAULT_TTL = timedelta(days=14)

_TOKEN_TYPE = "order_receipt"


def mint(order_id: uuid.UUID, *, ttl: timedelta = DEFAULT_TTL) -> str:
    """A receipt token for *order_id*, good for *ttl*."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(order_id),
        "type": _TOKEN_TYPE,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def order_id_from(token: str | None) -> uuid.UUID | None:
    """
    The order id a *token* proves ownership of, or None.

    None — never an exception — for anything that does not verify: an absent
    token, a bad signature, an expired one, a token of the wrong type, or a
    subject that is not a UUID. The caller falls back to the email-ownership
    path, so a failed token must read as "no proof offered" rather than an error
    the customer sees.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") != _TOKEN_TYPE:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(str(sub))
    except (ValueError, TypeError):
        return None
