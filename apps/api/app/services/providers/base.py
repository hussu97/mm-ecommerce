"""
What every card gateway has to look like from the inside of this application.

The point of this file is that `payment_service` never learns a gateway's
vocabulary. Stripe says `payment_intent.succeeded`; Ziina says
`payment_intent.status.updated` with `data.status == "completed"`; a third one
will say something else again. Each is translated *here*, at the edge, into the
same six-value `PaymentEventType`, and the code that confirms an order, mails
the customer and returns the stock reads only that.

That translation is the whole reason the seam holds. The previous version of
this interface returned `dict`, which is not an interface — it is a promise to
agree later, and what it produced downstream was `if event_type ==
"charge.refunded"` written inside the order logic, which is Stripe's schema
leaking into the part of the system that has opinions about cake.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from app.models.order import Order

__all__ = [
    "GatewayEvent",
    "GatewaySession",
    "GatewayUnavailableError",
    "PaymentEventType",
    "PaymentGatewayProvider",
]


class GatewayUnavailableError(RuntimeError):
    """
    The gateway could not be reached, or answered in a way that says the fault
    is theirs rather than the order's.

    Raised *only* for that. It is the signal the router fails over on, so
    raising it for a declined card or an amount below the floor would silently
    re-present a legitimately refused payment to a second processor — and, if
    that one took it, produce an order paid through a gateway nobody chose.
    Anything the customer or the order did wrong is a `BadRequestError`.
    """


class PaymentEventType(str, enum.Enum):
    """
    The only things a payment can tell us that this application acts on.

    Deliberately small. A gateway that reports twenty lifecycle transitions
    reports nineteen we do nothing about, and `UNHANDLED` is how it says so —
    acknowledged, journalled, and applied to nothing.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    UNHANDLED = "unhandled"


@dataclass(frozen=True)
class GatewaySession:
    """A checkout the customer can be sent to."""

    #: The handle we store and later recognise this attempt by.
    session_id: str
    #: Where to send the customer. Absolute, and theirs, not ours.
    checkout_url: str
    #: The handle refunds and disputes will quote, when the gateway mints it up
    #: front. Stripe does not — its Payment Intent only exists once the customer
    #: pays — so this is null there until the confirmation webhook arrives.
    payment_id: str | None = None
    #: The gateway's own word for the state it created the session in.
    raw_status: str | None = None


@dataclass(frozen=True)
class GatewayRefund:
    """Money sent back, as the gateway acknowledged it."""

    #: The gateway's handle for the refund itself, not the payment. Stored so a
    #: second attempt can be recognised and so a dashboard conversation has a
    #: number in it.
    refund_id: str
    #: What was actually sent back, in the order's currency. Read from the
    #: gateway's reply rather than echoed from the request: a processor is
    #: entitled to refund less than asked, and believing our own number would
    #: leave the books saying something the bank does not.
    amount: Decimal
    #: `pending`, `completed` or `failed` — Ziina's three, which Stripe's
    #: statuses are mapped onto. A refund is frequently not instant, so
    #: `pending` is a success here and the webhook settles it later.
    status: str
    #: The gateway's own word, kept for the log.
    raw_status: str | None = None


@dataclass(frozen=True)
class GatewayEvent:
    """One webhook, said in this application's words."""

    #: Unique per event, per gateway, and stable across redeliveries — it is what
    #: the dedup index in `webhook_events` is built on. A gateway that does not
    #: issue event IDs (Ziina does not) must synthesise one from the fields that
    #: identify the transition, or a retry will be processed twice.
    event_id: str
    event_type: PaymentEventType
    #: What the gateway actually called it, for the log and the journal row.
    raw_type: str

    #: How to find the order. In descending order of how much we trust it:
    #: metadata we put there ourselves, then the handles on
    #: `payment_transactions`. Ziina carries no metadata at all, which is
    #: precisely why the handles have to be enough on their own.
    order_number: str | None = None
    session_id: str | None = None
    payment_id: str | None = None

    #: Refund detail, in minor units, as the gateway reports it. A refund is not
    #: automatically the whole order — see `_is_full_refund`.
    amount_refunded: int | None = None
    amount_captured: int | None = None
    fully_refunded: bool | None = None

    #: Why it failed, when it failed and the gateway said why.
    error_code: str | None = None
    error_message: str | None = None


class PaymentGatewayProvider(ABC):
    """Everything a card processor has to be able to do to be routable."""

    #: Matches `PaymentGatewayEnum` and the `payment_gateways.code` column.
    code: str

    def is_configured(self) -> bool:
        """
        Whether this environment holds enough to actually charge a card here.

        Checked by the router *in addition to* the `is_active` flag, and the
        distinction is the safety property that keeps Ziina off production: a
        row can be switched on by anyone with the admin, and credentials cannot.
        An active gateway with no keys is not usable, and saying so here means
        the failure is a clean skip rather than a 500 in the middle of checkout.
        """
        return True

    @abstractmethod
    async def create_session(
        self,
        order: Order,
        *,
        test_mode: bool = False,
    ) -> GatewaySession:
        """
        Create a checkout for *order* and return where to send the customer.

        Async because it is a network call made while a customer waits, and every
        other outbound integration here (`lalamove_provider`, `noon_send_provider`,
        `turnstile_service`) is already `httpx.AsyncClient`. A blocking call in
        here does not just slow the checkout down — it stalls the whole worker,
        serving nothing at all for the duration of the timeout. That is worst
        exactly when it matters most: a gateway having a bad day is a gateway
        answering slowly, and the failover is supposed to be what saves the
        checkout, not a second thing to wait on.

        Raises `GatewayUnavailableError` when the gateway is at fault (and the
        router should try the next one), and `BadRequestError` when the order is
        (and it should not).
        """

    @abstractmethod
    def parse_webhook(self, payload: bytes, headers: Mapping[str, str]) -> GatewayEvent:
        """
        Verify the signature on an incoming webhook and translate it.

        Takes the whole header mapping rather than one signature string because
        gateways disagree about how many headers the proof is spread across, and
        a parameter list that assumes one of them is an interface that has to
        change for the second gateway.

        Raises `BadRequestError` on a bad signature or an unreadable body —
        both of which are permanent, and neither of which a retry fixes.
        """

    def minimum_amount(self) -> Decimal | None:
        """
        A floor the gateway enforces itself, if the row does not name one.

        The `payment_gateways` row is the authority; this is the fallback for a
        gateway whose row was created without one, so a hard refusal at the
        processor still becomes a sentence the customer can act on.
        """
        return None

    async def refund(
        self,
        *,
        payment_id: str,
        amount: Decimal,
        idempotency_key: str,
        test_mode: bool = False,
    ) -> GatewayRefund:
        """
        Send *amount* back to the card that paid `payment_id`.

        `idempotency_key` is not optional and not decoration. This is called from
        a status transition that a webhook, a retry or two admins on two laptops
        can all reach, and the failure it prevents is paying a customer twice.
        Both gateways take one — Stripe as a header, Ziina as the refund's own
        client-generated `id` — so the key is derived from the order and the
        amount rather than randomly, and the same refund asked for twice is one
        refund.

        Raises `GatewayUnavailableError` when the processor is at fault and the
        caller may try again, and `BadRequestError` when the request is wrong in
        a way retrying will not fix — an already-refunded charge, an amount
        larger than the payment.
        """
        raise NotImplementedError(f"{self.code} cannot issue refunds")
