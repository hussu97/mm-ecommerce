"""
In-page Apple Pay on the storefront checkout, for a named allowlist of testers.

This is the one place the storefront is allowed to know it is talking to Stripe.
Everywhere else a card is a card and which processor settles it is the router's
business (`payment_gateway_router`) — but an Apple Pay sheet is a Stripe-specific
surface, drawn by Stripe.js against a PaymentIntent, so the feature exists only
when Stripe is the active card gateway and only for the accounts named below.

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

from app.core.exceptions import BadRequestError, ForbiddenError
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

#: The accounts allowed to see and use in-page Apple Pay.
#:
#: An explicit allowlist rather than a role, because this is a pre-release test
#: surface for named people, not a permission the shop grants. A constant rather
#: than an env var on purpose: it is not a secret, and a var forgotten in one
#: environment silently becoming an empty allowlist there is the compose-env
#: trap (W9) pointed the other way — a gate that quietly opens to nobody.
APPLE_PAY_TEST_EMAILS = frozenset({"h_abbasi97@hotmail.com"})

#: The floor eligibility is coarsely checked against when the caller names no
#: amount. Stripe refuses an AED charge under 2.00 at its own edge, so this is
#: the smallest amount any order could ever be routed at — enough to answer
#: "is Stripe the active card gateway at all", which is all eligibility is for.
#: The intent endpoint re-checks against the real order total and is the actual
#: guard.
_ELIGIBILITY_PROBE_AMOUNT = Decimal("2.00")


def is_test_user(user: User | None) -> bool:
    """Whether *user* is a signed-in account on the Apple Pay allowlist."""
    if user is None or getattr(user, "is_guest", False):
        return False
    email = (getattr(user, "email", "") or "").strip().lower()
    return email in APPLE_PAY_TEST_EMAILS


async def _stripe_is_default_card_gateway(db: AsyncSession, amount: Decimal) -> bool:
    """
    True when the gateway that would settle a card of *amount* is Stripe.

    Reads the same `candidates()` the real payment does, so the answer tracks an
    admin toggling gateways during an incident without anything shipping — the
    moment Ziina is made the default, Apple Pay stops being offered.
    """
    options = await payment_gateway_router.candidates(db, amount)
    return bool(options) and options[0].code == stripe_provider.code


async def eligibility(
    db: AsyncSession, user: User | None, *, amount: Decimal | None = None
) -> dict:
    """
    Whether this caller may be offered in-page Apple Pay.

    Two gates, both server-side: the account is on the allowlist, and Stripe is
    the active card gateway. The browser's own "can it actually do Apple Pay"
    check is the client's to make — it needs the device, and the server cannot
    see it.
    """
    if not is_test_user(user):
        return {"eligible": False}
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

    Enforces the allowlist and order ownership, refuses an order that is
    cancelled or already paid, resets a `payment_failed` order to `created` for
    a retry (mirroring `payment_service.create_session`), and refuses unless
    Stripe is the active card gateway for the order's amount — Apple Pay is a
    Stripe surface and Ziina does not offer it.

    Returns the `client_secret` the browser confirms against, plus the
    server-computed amount so the Apple Pay sheet displays the figure the card
    is actually charged rather than one the client re-derived.
    """
    if not is_test_user(user):
        # A hard refusal, not a quiet `eligible: false`: this is the endpoint
        # that spends money, and a caller reaching it is not one the client
        # gate let through.
        raise ForbiddenError("Apple Pay is not available for this account")

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
