"""
In-page Apple Pay on the storefront checkout.

This is the one place the storefront is allowed to know it is talking to Stripe.
Everywhere else a card is a card and which processor settles it is the router's
business (`payment_gateway_router`) — but an Apple Pay sheet is a Stripe-specific
surface, drawn by Stripe.js against a PaymentIntent, so the feature exists only
when Stripe is the active card gateway. Beyond that it is offered to everyone
the device and the gateway allow; the browser decides whether Apple Pay is
actually available and the gateway check below decides whether Stripe is settling
cards, and there is no account gate above either.

The money path is deliberately the ordinary one. The PaymentIntent minted here
carries the order number in its metadata, so `payment_intent.succeeded`
reconciles it through exactly the webhook path every hosted-Checkout card
payment already uses (`payment_service._handle_payment_succeeded`). Nothing
below the intent is Apple-Pay-aware, and this module writes no order status of
its own — it only records the attempt the webhook will later settle.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.order import OrderStatusEnum
from app.models.payment_transaction import (
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from app.models.user import User
from app.services.orders import order_lifecycle
from app.services.payments import payment_gateway_router
from app.services.payments.payment_methods import CARD
from app.services.payments.payment_service import (
    _assert_may_act_on,
    _is_paid,
    _load_order,
)
from app.services.providers.base import GatewayUnavailableError
from app.services.providers.stripe_provider import provider as stripe_provider

logger = logging.getLogger(__name__)

#: The floor eligibility is coarsely checked against when the caller names no
#: amount. Stripe refuses an AED charge under 2.00 at its own edge, so this is
#: the smallest amount any order could ever be routed at — enough to answer
#: "is Stripe the active card gateway at all", which is all eligibility is for.
#: The intent endpoint re-checks against the real order total and is the actual
#: guard.
_ELIGIBILITY_PROBE_AMOUNT = Decimal("2.00")


async def _stripe_is_default_card_gateway(db: AsyncSession, amount: Decimal) -> bool:
    """
    True when the gateway that would settle a card of *amount* is Stripe.

    Reads the same `candidates()` the real payment does, so the answer tracks an
    admin toggling gateways during an incident without anything shipping — the
    moment Ziina is made the default, Apple Pay stops being offered.
    """
    options = await payment_gateway_router.candidates(db, amount)
    return bool(options) and options[0].code == stripe_provider.code


async def eligibility(db: AsyncSession, *, amount: Decimal | None = None) -> dict:
    """
    Whether in-page Apple Pay may be offered here at all.

    One server-side gate: Stripe is the active card gateway (Apple Pay is a
    Stripe surface, and Ziina does not offer it). Not account-specific — anyone
    checking out is offered it — so the browser's own "can this device actually
    do Apple Pay" check, which the client makes on top of this, is what narrows
    it to the devices that can.
    """
    probe = amount if amount and amount > 0 else _ELIGIBILITY_PROBE_AMOUNT
    return {"eligible": await _stripe_is_default_card_gateway(db, probe)}


def _intent_attempt(order, payment_id: str) -> PaymentTransaction | None:
    """
    The existing attempt this intent belongs to, if there is one.

    The intent is minted with `idempotency_key=pi_applepay_{order_number}`, so a
    customer who dismisses the sheet and taps again gets Stripe's *same* intent
    back — same `pi_…` id. Reusing the row rather than inserting a second keeps
    it clear of `uq_payment_transactions_gateway_session` semantics and is the
    truthful reading: it is the same attempt, not a new one.
    """
    for transaction in order.payment_transactions:
        if transaction.gateway == stripe_provider.code and (
            transaction.payment_id == payment_id
        ):
            return transaction
    return None


async def create_intent(db: AsyncSession, order_number: str, user: User) -> dict:
    """
    Mint a Stripe PaymentIntent for an order so the browser can take Apple Pay.

    Enforces order ownership, refuses an order that is cancelled or already
    paid, resets a `payment_failed` order to `created` for a retry (mirroring
    `payment_service.create_session`), and refuses unless Stripe is the active
    card gateway for the order's amount — Apple Pay is a Stripe surface and
    Ziina does not offer it.

    Returns the `client_secret` the browser confirms against, plus the
    server-computed amount so the Apple Pay sheet displays the figure the card
    is actually charged rather than one the client re-derived.
    """
    order = await _load_order(db, order_number)
    _assert_may_act_on(order, user.id, admin=user.is_admin)

    if order.status == OrderStatusEnum.CANCELLED:
        raise BadRequestError("Cannot pay for a cancelled order")

    if order.status == OrderStatusEnum.PAYMENT_FAILED:
        await order_lifecycle.transition(db, order, OrderStatusEnum.CREATED)
        await db.flush()

    if _is_paid(order):
        raise BadRequestError("Order has already been paid")

    total = Decimal(str(order.total))
    if total <= Decimal("0.00"):
        # A zero-total order needs no payment at all — the ordinary
        # `create-session` path confirms it on the spot. There is nothing here
        # for Apple Pay to charge.
        raise BadRequestError("This order has nothing to pay")

    options = await payment_gateway_router.candidates(db, total)
    if not options or options[0].code != stripe_provider.code:
        # Either no card gateway can take this amount, or the active one is not
        # Stripe. Apple Pay is Stripe-only, so there is nothing to draw.
        raise BadRequestError("Apple Pay is unavailable for this order right now")

    try:
        intent = await stripe_provider.create_payment_intent(
            order, idempotency_key=f"pi_applepay_{order.order_number}"
        )
    except GatewayUnavailableError as exc:
        raise BadRequestError(
            "Apple Pay is temporarily unavailable. Please try again shortly."
        ) from exc

    # Record the attempt the same way `_create_card_session` records a hosted
    # session, so `payment_intent.succeeded` finds a row to settle. The intent
    # id goes on the transaction — deliberately NOT on `order.payment_id`, where
    # a bare `pi_…` reads as "paid" (`payment_service._is_paid`) before a single
    # dirham has moved. The webhook writes it onto the order when it confirms.
    transaction = _intent_attempt(order, intent.id)
    if transaction is None:
        transaction = PaymentTransaction(
            order_id=order.id,
            gateway=stripe_provider.code,
            amount=total,
            currency="AED",
        )
        order.payment_transactions.append(transaction)
        db.add(transaction)

    transaction.status = PaymentTransactionStatusEnum.PENDING.value
    transaction.payment_id = intent.id
    transaction.amount = total
    transaction.raw_status = getattr(intent, "status", None)

    order.payment_method = CARD
    order.payment_provider = stripe_provider.code
    await db.flush()

    logger.info(
        "Apple Pay intent created: order=%s intent=%s",
        order.order_number,
        intent.id,
    )

    return {
        "client_secret": intent.client_secret,
        "amount": f"{total:.2f}",
        "currency": "AED",
        "order_number": order.order_number,
    }
