"""
What the customer chose, whatever word arrived for it.

Its own module, importing nothing but the enum and the exception type, because
both `order_service` (which writes the value) and `payment_service` (which acts
on it) need the same answer and importing either from the other is a cycle.

A single function rather than a constant in two places: the legacy aliases are
the sort of list that grows a fourth entry in one file and not the other, and
the failure mode of that is an order stamped `stripe` that the payment path
refuses to recognise as a card.
"""

from __future__ import annotations

from app.core.exceptions import BadRequestError
from app.models.payment_gateway import PaymentMethodEnum

__all__ = ["CARD", "COD", "normalise_method"]

CARD = PaymentMethodEnum.CARD.value
COD = PaymentMethodEnum.COD.value

#: Words that have meant "card" at some point and may still arrive.
#:
#: `stripe` is what the checkout posted and what every card order written before
#: the method/gateway split still carries, so it turns up from a browser holding
#: the previous bundle *and* from a retry of an old order. `ziina` is here for
#: symmetry — nothing sends it today, and if a future client ever names a
#: gateway where a method belongs, it should be read as the method rather than
#: honoured as a routing request.
_CARD_ALIASES = frozenset({"card", "stripe", "ziina"})


def normalise_method(value: str | None) -> str:
    """
    `card` or `cod`. Raises `BadRequestError` on anything else.

    An empty value is a card, which is the historical default and the only
    choice available for delivery.
    """
    raw = str(getattr(value, "value", value) or "").strip().lower()
    if raw == COD:
        return COD
    if raw in _CARD_ALIASES or not raw:
        return CARD
    raise BadRequestError(f"Unknown payment method '{raw}'. Supported: {CARD}, {COD}")
