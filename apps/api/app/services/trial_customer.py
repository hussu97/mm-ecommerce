"""
The accounts running live tests against production.

A courier integration cannot be finished in staging. Staging has no real
addresses, no real baskets, and no way to find out that a Google-formatted
address line is 12 characters too long for somebody's API. The only way to
learn those things is to place real orders on the real site — which means one
account has to behave differently from every other, on production, deliberately.

Two things follow from being on this list, and they are two halves of the same
decision:

* **noon Send carries the order.** Everyone else in a noon Send zone is carried
  by Lalamove and notices nothing. See `courier_service.may_use_noon_send`.
* **Delivery is free.** These orders exist to exercise the pipeline, not to be
  charged for, and a test that costs the tester money is a test that gets run
  less often than it should.

An empty list turns both off — which is how the trial ends: nobody is special,
noon Send opens to every customer in its zone, and no one gets free delivery.

The membership test is deliberately strict about identity. Being signed in is
half of it, and it is the half that cannot be forged: an email address is a
string anybody may type into a guest checkout, and a guest typing this one must
not get free delivery on a real order.
"""

from __future__ import annotations

import uuid

from app.core.config import settings

__all__ = ["emails", "is_trial_customer"]


def emails() -> set[str]:
    raw = settings.TRIAL_CUSTOMER_EMAILS or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_trial_customer(user_id: uuid.UUID | None, email: str | None) -> bool:
    """Whether this order was placed by a signed-in account on the trial list."""
    if user_id is None:
        return False
    allowed = emails()
    return bool(allowed) and (email or "").strip().lower() in allowed
