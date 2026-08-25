"""
One row per attempt to take money for an order, on one gateway.

The order used to carry the whole story in two nullable strings —
`payment_provider` and `payment_id` — and both were overwritten on every retry.
That worked while there was exactly one processor and its IDs happened to encode
their own state: a Stripe Checkout Session is `cs_…` and the Payment Intent it
becomes is `pi_…`, so `payment_id.startswith("pi_")` could stand in for "this
order is paid".

It cannot stand in for it any more. Ziina mints its payment intent up front, at
the moment a Stripe *session* would be minted, so the same ID is present on an
order nobody has paid for. Reading state off an ID prefix was always inference;
with two gateways it is inference that is wrong.

So paid-ness becomes a fact with a row behind it, and the row carries what a
prefix never could: which gateway, how much, what the gateway called it, and why
it failed. A failover leaves two rows and the reason the first one was
abandoned, which is the question anyone asks first when a checkout misbehaves.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary

__all__ = [
    "PaymentTransaction",
    "PaymentTransactionStatusEnum",
    "PaymentFailureReason",
]


class PaymentTransactionStatusEnum(str, enum.Enum):
    """
    Where one attempt got to. Normalised across gateways on purpose — the
    processor's own word for it is kept verbatim in `raw_status` next door.
    """

    #: A session exists and the customer has not finished with it.
    PENDING = "pending"
    #: Money moved.
    SUCCEEDED = "succeeded"
    #: The gateway refused, or the customer's bank did.
    FAILED = "failed"
    #: The customer walked away, or we abandoned this attempt to fail over.
    CANCELLED = "cancelled"
    #: Money moved and then moved back.
    REFUNDED = "refunded"
    #: A chargeback was filed against it.
    DISPUTED = "disputed"


class PaymentFailureReason(str, enum.Enum):
    """
    Why a card was refused, in the small set of things a *customer* can act on.

    This is the same idea as the status enum above, one level down: a gateway's
    decline vocabulary is large (Stripe alone has ~45 `decline_code`s) and most
    of the distinctions in it are for a bank's reconciliation, not a shopper's
    next move. `insufficient_funds` and `expired_card` mean "try another card";
    `incorrect_cvc` means "check the digits"; a dozen unknowable bank refusals
    all mean "contact your bank or try another card". These buckets are those
    next moves, so the storefront can localise one short, honest sentence per
    bucket while the gateway's raw code stays in `error_code` for reconciling.

    Translated *at the provider* (see `providers/base.py`), like every other
    gateway word: Stripe maps its `decline_code`/`code` onto these; a gateway
    with no decline taxonomy (Ziina reports only an HTTP status and a human
    message) sets none and lets its own message through verbatim.

    Deliberately conservative about what it reveals. Stripe's guidance is that
    `fraudulent`, `lost_card` and `stolen_card` must be shown as an ordinary
    decline, never named — so they map to `CARD_DECLINED` with everyone else,
    and the customer is never told their card was flagged.
    """

    #: Not enough on the card — includes velocity/credit-limit refusals.
    INSUFFICIENT_FUNDS = "insufficient_funds"
    #: Card expired, or an expiry that cannot be right.
    EXPIRED_CARD = "expired_card"
    #: The security code was wrong.
    INCORRECT_CVC = "incorrect_cvc"
    #: The card number was wrong.
    INCORRECT_NUMBER = "incorrect_number"
    #: Billing postcode/address did not match the card.
    INCORRECT_DETAILS = "incorrect_details"
    #: The card or its currency is not usable for this purchase.
    CARD_NOT_SUPPORTED = "card_not_supported"
    #: The bank wants 3-D Secure / step-up authentication.
    AUTHENTICATION_REQUIRED = "authentication_required"
    #: A transient processor error — the one bucket where "try again" is the
    #: honest advice rather than "try another card".
    PROCESSING_ERROR = "processing_error"
    #: A near-identical charge went through moments ago.
    DUPLICATE = "duplicate"
    #: The catch-all: a bank refusal with no customer-actionable detail, and the
    #: home of every reason we must not name (fraud, lost, stolen).
    CARD_DECLINED = "card_declined"


#: The statuses that mean this attempt paid for the order. `REFUNDED` is
#: deliberately absent: a refunded order was paid and is not paid *now*, and the
#: question this set answers ("may we create another session") wants now.
SETTLED_STATUSES = frozenset({PaymentTransactionStatusEnum.SUCCEEDED.value})


class PaymentTransaction(Base, UUIDMixin, TimestampMixin):
    """An attempt against one gateway, for one order."""

    __tablename__ = "payment_transactions"
    __table_args__ = (
        # A gateway's own handles are unique within that gateway and not across
        # them — Ziina and Stripe both mint IDs beginning `pi_`, and a global
        # unique index on the bare string would eventually collide two unrelated
        # payments into one another. Partial, because both columns are legitimately
        # null: a session that never got an ID, a gateway that has no separate
        # confirmed-payment handle.
        Index(
            "uq_payment_transactions_gateway_session",
            "gateway",
            "session_id",
            unique=True,
            postgresql_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "uq_payment_transactions_gateway_payment",
            "gateway",
            "payment_id",
            unique=True,
            postgresql_where=text("payment_id IS NOT NULL"),
        ),
        # Migration 099. Our normalised vocabulary only — the gateway's own
        # word lives unconstrained in `raw_status`, by design.
        status_vocabulary(
            "payment_transactions", "status", PaymentTransactionStatusEnum
        ),
        # Our normalised failure buckets. Nullable — most attempts never fail,
        # and a gateway with no taxonomy leaves it null. The raw gateway code is
        # unconstrained in `error_code`, same split as status/raw_status above.
        status_vocabulary(
            "payment_transactions",
            "failure_reason",
            PaymentFailureReason,
            nullable=True,
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: `PaymentGatewayEnum` — which processor this attempt was made against.
    gateway: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    #: The handle the customer is sent to pay against. A Stripe Checkout Session
    #: (`cs_…`); a Ziina Payment Intent (which is also the payment handle).
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: The handle that identifies the money once it moves, and the one refunds
    #: and disputes arrive quoting. Stripe fills this in only at confirmation;
    #: Ziina has it from the start.
    payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The gateway's handle for the *refund*, when one has been issued against
    #: this attempt. Distinct from `payment_id`, which identifies the charge —
    #: a dashboard conversation about money going back quotes this one.
    refund_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentTransactionStatusEnum.PENDING.value,
        server_default=PaymentTransactionStatusEnum.PENDING.value,
        index=True,
    )

    #: What the gateway called it. Kept because our six statuses are a lossy
    #: projection of theirs, and the lost detail is exactly what is wanted when
    #: reconciling one of ours against one of theirs.
    raw_status: Mapped[str | None] = mapped_column(String(60), nullable=True)

    #: What we asked to be charged, snapshotted at session creation. Not read
    #: back from the order, because the order's total can be edited afterwards
    #: and the question here is what the customer was actually shown.
    amount: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="AED"
    )

    #: Where the customer was sent. Kept so a support conversation about a
    #: checkout that "did nothing" can be answered with the actual link.
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: `error_code` normalised into the bucket the customer is actually shown.
    #: Null on a success, on an abandoned attempt, and on a gateway that gives
    #: no decline taxonomy to normalise (Ziina — there `error_message` is shown
    #: verbatim instead). The raw code stays in `error_code` for reconciling.
    failure_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)

    order = relationship("Order", back_populates="payment_transactions")

    @property
    def is_settled(self) -> bool:
        """Whether this attempt is the one that paid for the order."""
        return self.status in SETTLED_STATUSES

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return (
            f"<PaymentTransaction {self.gateway} {self.status} "
            f"session={self.session_id} payment={self.payment_id}>"
        )
