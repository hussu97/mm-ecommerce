"""
Which processor carries a card payment, and the switch that decides.

The customer picks a *method* — card or cash. Who settles the card is ours to
decide, and until now it was decided by an import: `payment_service` held a
one-entry registry and `create_session` was handed the literal string
`"stripe"` by the checkout page. A Stripe incident is then a deploy, at the
exact moment a deploy is hardest.

This table is the seam. It is deliberately shaped like `couriers`, which solves
the same problem one layer over: a commercial relationship that changes without
the code changing, so the code should not be where it is written down.
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

__all__ = ["PaymentGateway", "PaymentGatewayEnum", "PaymentMethodEnum"]


class PaymentGatewayEnum(str, enum.Enum):
    """The processors that can settle a card payment."""

    STRIPE = "stripe"
    ZIINA = "ziina"


class PaymentMethodEnum(str, enum.Enum):
    """
    What the customer chose, which is not what processed it.

    `CARD` is the whole of the card estate — a customer who paid by card on
    Stripe and one who paid by card on Ziina made the same choice and should
    read the same way on their order, in the admin, and in every breakdown.
    The gateway lives on `orders.payment_provider`.

    `STRIPE` is the value this column held for every card order written before
    the split, kept as an accepted alias so an in-flight checkout and a retry of
    an old order both still work. Nothing new is written with it.
    """

    CARD = "card"
    COD = "cod"

    # ── Legacy, accepted on the way in, never written ────────────────────────
    STRIPE = "stripe"
    TABBY = "tabby"
    TAMARA = "tamara"


class PaymentGateway(Base, UUIDMixin, TimestampMixin):
    """
    A card processor and the terms on which we are willing to send it traffic.

    A row existing is not permission to use it. Three things have to agree
    before the router will pick a gateway, and they fail independently on
    purpose: the row is `is_active` (an operator said so), the provider is
    *configured* (credentials actually exist in this environment), and the order
    total is inside `[min_amount, max_amount]`. Production ships Ziina present,
    inactive and unconfigured — three locks on a door that is only there so it
    can be opened later, on purpose, by someone who means it.
    """

    __tablename__ = "payment_gateways"

    #: Matches `PaymentGatewayEnum` and the key in the provider registry. The
    #: join between a row an operator can edit and code that can charge a card.
    code: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)

    #: Whether the router may send this gateway traffic at all.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    #: Lower goes first. The order the router walks when choosing, and the order
    #: it walks again when the first choice will not answer.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    #: Whether this gateway may be reached for *automatically*, after another
    #: one failed to produce a session.
    #:
    #: Separate from `is_active` because they answer different questions. Active
    #: is "may we use it"; this is "may we use it without anyone deciding to".
    #: A gateway with worse rates is a fine deliberate choice and a bad reflex.
    supports_failover: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    #: The smallest total this gateway will accept, in AED.
    #:
    #: Both processors refuse under 2.00 and both refuse it *at the gateway*,
    #: which turns a 1.50 order into an opaque 400 at the last screen of
    #: checkout. Held here so the refusal happens with a sentence the customer
    #: can act on, and so a processor changing its floor is an edit.
    min_amount: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)

    #: The largest total, where one applies. Null means no ceiling.
    max_amount: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)

    #: Whether sessions are created against the gateway's sandbox.
    #:
    #: Ziina takes this per payment intent (`test: true`) rather than per key,
    #: so it has to be a value we send rather than a credential we hold. Stripe
    #: reads it from the key itself and ignores this.
    test_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        state = "active" if self.is_active else "inactive"
        return f"<PaymentGateway {self.code} ({state}, priority={self.priority})>"
