"""
Taking money for an online order, without knowing who is taking it.

This module used to be a Stripe integration with a `provider` argument that only
ever had one useful value. It now knows about *methods* — card, cash, nothing to
pay — and delegates the question of which processor settles a card to
`payment_gateway_router`, which reads it out of a table.

The seam that makes this work is `providers/base.py`: every gateway hands back
the same `GatewaySession` and the same `GatewayEvent`, so everything below
`_apply_event` is written once and is the same code whether the money came
through Stripe or Ziina. There is deliberately no `if gateway == "stripe"` in
this file, and adding one would be the first step back to where it started.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.payment_transaction import (
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from app.models.webhook_event import WebhookEvent
from app.services import email_service, order_service, payment_gateway_router
from app.services.payment_methods import CARD, COD, normalise_method
from app.services.providers.base import (
    GatewayEvent,
    GatewayUnavailableError,
    PaymentEventType,
)
from app.services.providers.stripe_provider import provider as stripe_provider

__all__ = [
    "COD",
    "create_session",
    "get_status",
    "handle_webhook",
    "normalise_method",
]

logger = logging.getLogger(__name__)

#: The statuses a successful payment may legitimately move an order out
#: of. Everything else is either already done or deliberately finished, and a
#: webhook must not walk it backwards — see `_handle_payment_succeeded`.
_CONFIRMABLE_FROM = {
    OrderStatusEnum.CREATED,
    OrderStatusEnum.PAYMENT_FAILED,
}


async def _load_order(db: AsyncSession, order_number: str) -> Order:
    stmt = (
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.payment_transactions))
        .where(Order.order_number == order_number)
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(f"Order '{order_number}' not found")
    return order


async def _load_order_by_handle(
    db: AsyncSession,
    gateway: str,
    *,
    payment_id: str | None = None,
    session_id: str | None = None,
) -> Order:
    """
    Find the order a gateway handle belongs to.

    This is the correlation path for a gateway that carries no metadata — which
    is to say for Ziina, whose `CreatePaymentIntentDto` has nowhere at all to
    put an order number. The handle we stored when we created the session is the
    only link, so it has to be a reliable one.

    `payment_transactions` is asked first because it is the record built for
    this, and `orders.payment_id` second because orders written before that
    table existed have nothing else. Both are scoped by gateway where they can
    be: Stripe and Ziina both mint IDs beginning `pi_`, and an unscoped lookup
    would eventually confirm the wrong order.
    """
    handles = [h for h in (payment_id, session_id) if h]
    if not handles:
        raise NotFoundError("Webhook carried no payment handle to match on")

    stmt = (
        select(Order)
        .join(PaymentTransaction, PaymentTransaction.order_id == Order.id)
        .options(selectinload(Order.items), selectinload(Order.payment_transactions))
        .where(
            PaymentTransaction.gateway == gateway,
            (PaymentTransaction.payment_id.in_(handles))
            | (PaymentTransaction.session_id.in_(handles)),
        )
        .limit(1)
    )
    order = (await db.execute(stmt)).scalars().first()
    if order is not None:
        return order

    legacy = (
        (
            await db.execute(
                select(Order)
                .options(
                    selectinload(Order.items), selectinload(Order.payment_transactions)
                )
                .where(Order.payment_id.in_(handles))
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if legacy is not None:
        return legacy

    raise NotFoundError(f"No order found for {gateway} handle {handles[0]}")


def _assert_may_act_on(
    order: Order, user_id: uuid.UUID | None, admin: bool = False
) -> None:
    """
    Refuse to touch an order the caller does not own.

    Order numbers are `MM-YYYYMMDD-NNN` — the day plus a counter — so anyone
    can write down a valid one. Without this check, guessing a pickup order's
    number was enough to POST `{"provider": "cod"}` at it and move it to
    CONFIRMED: the shop bakes a cake, sends the customer a confirmation, and
    nobody has paid. The same guess also reset a `payment_failed` order back to
    `created`, reopening a payment window on someone else's order.

    Checkout always mints a user before it creates the order (`ensureCheckoutAuth`
    on the web side), guest or otherwise, so every legitimate caller arrives
    holding the token of the order's own `user_id`.
    """
    if admin:
        return
    if user_id is None or order.user_id != user_id:
        raise ForbiddenError("Not your order")


def _is_paid(order: Order) -> bool:
    """
    Whether money has actually moved for this order.

    A settled `payment_transactions` row is the answer, and the legacy prefix
    check behind it is only for orders written before that table existed —
    those carry a bare `pi_…` on `orders.payment_id` and nothing else, and
    dropping the fallback would silently reopen a payment window on every one
    of them.

    The fallback is scoped to Stripe on purpose. Ziina mints its payment intent
    at session creation, so `pi_…` is present on a Ziina order the instant the
    customer is redirected — reading that as "paid" would mark every abandoned
    Ziina checkout as settled.
    """
    if any(t.is_settled for t in order.payment_transactions):
        return True
    if order.payment_provider in (None, "stripe"):
        return stripe_provider.is_confirmed_payment_id(order.payment_id)
    return False


async def create_session(
    db: AsyncSession,
    order_number: str,
    method: str,
    *,
    user_id: uuid.UUID | None = None,
    admin: bool = False,
) -> dict:
    """
    Create a payment checkout session for the given order.

    *method* is what the customer chose — `card` or `cod` — not a gateway.
    Which processor settles a card is chosen here, from the `payment_gateways`
    table, and reported back in the response so the caller can log it. The
    caller does not get to ask for one.
    """
    order = await _load_order(db, order_number)
    _assert_may_act_on(order, user_id, admin)
    method = normalise_method(method)

    if order.status == OrderStatusEnum.CANCELLED:
        raise BadRequestError("Cannot create payment session for a cancelled order")

    # Allow retry: reset payment_failed orders back to created
    if order.status == OrderStatusEnum.PAYMENT_FAILED:
        order.status = OrderStatusEnum.CREATED
        await db.flush()

    # Idempotency: if already has a confirmed payment, reject
    if _is_paid(order):
        raise BadRequestError("Order has already been paid")

    # Zero-total orders (100% discount) are confirmed immediately — no payment needed.
    order_total = (
        Decimal(str(order.total))
        if not isinstance(order.total, Decimal)
        else order.total
    )
    if order_total <= Decimal("0.00"):
        order.status = OrderStatusEnum.CONFIRMED
        await db.flush()
        order_response = await order_service.to_response(db, order)
        await email_service.send_order_confirmation(order_response)
        await email_service.send_owner_order_notification(order_response)
        return {
            "provider": "none",
            "session_id": None,
            "checkout_url": None,
            "confirmed": True,
        }

    # Cash on delivery: nothing to charge now, so the order is accepted on the
    # spot and the money is collected at the door. Cash is how most of this
    # market pays for food, and requiring a card up front turns those
    # customers away at the last screen.
    if method == COD:
        # Cash is offered for collection only: the customer pays at the counter
        # when they pick the order up. There is no cash handling on the delivery
        # side, so a cash *delivery* must not be creatable even by hand.
        if order.delivery_method != DeliveryMethodEnum.PICKUP:
            raise BadRequestError(
                "Cash payment is only available for store pickup orders"
            )
        # `create_order` already confirms a cash order and sends its
        # confirmation, so the usual path arrives here with the work done and
        # must not mail the customer a second time. This branch remains for a
        # cash order that reached `created` some other way.
        already_confirmed = order.status == OrderStatusEnum.CONFIRMED
        order.status = OrderStatusEnum.CONFIRMED
        order.payment_method = COD
        order.payment_provider = COD
        order.payment_id = None
        await db.flush()
        if not already_confirmed:
            order_response = await order_service.to_response(db, order)
            await email_service.send_order_confirmation(order_response)
            await email_service.send_owner_order_notification(order_response)
        logger.info("Cash-on-delivery order confirmed: order=%s", order_number)
        return {
            "provider": COD,
            "session_id": None,
            "checkout_url": None,
            "confirmed": True,
        }

    return await _create_card_session(db, order, order_total)


async def _create_card_session(
    db: AsyncSession, order: Order, order_total: Decimal
) -> dict:
    """
    Route a card payment to a gateway, falling over to the next on an outage.

    The failover is the entire reason this feature exists, so it is worth being
    precise about what it will and will not do. A `GatewayUnavailableError` —
    a connection that never landed, a 502, a 201 with no redirect URL — means
    the processor never got as far as an opinion about this order, so asking a
    different one costs nothing and saves the checkout. Anything else is an
    opinion: a declined card, an amount out of range, a malformed request. Those
    propagate, because a second processor is not a second chance at a refusal.

    Every attempt leaves a row, including the abandoned ones. "Which gateway did
    this order try, in what order, and what did each one say" is the first
    question anyone asks about a checkout that misbehaved, and before this table
    it had no answer at all.
    """
    options = await payment_gateway_router.candidates(db, order_total)
    if not options:
        # Re-asks so the customer gets the specific reason — too small, versus
        # nothing available — rather than a generic failure.
        await payment_gateway_router.select_gateway(db, order_total)

    choice = options[0]
    tried: list[str] = []

    while choice is not None:
        tried.append(choice.code)
        transaction = PaymentTransaction(
            order_id=order.id,
            gateway=choice.code,
            status=PaymentTransactionStatusEnum.PENDING.value,
            amount=order_total,
            currency="AED",
        )
        db.add(transaction)

        try:
            session = choice.provider.create_session(order, test_mode=choice.test_mode)
        except GatewayUnavailableError as exc:
            transaction.status = PaymentTransactionStatusEnum.FAILED.value
            transaction.error_code = "gateway_unavailable"
            transaction.error_message = str(exc)[:2000]
            await db.flush()
            logger.error(
                "Gateway '%s' could not create a session for %s: %s",
                choice.code,
                order.order_number,
                exc,
            )
            choice = payment_gateway_router.failover_after(tried, options)
            if choice is not None:
                logger.warning(
                    "Failing over from '%s' to '%s' for order %s",
                    tried[-1],
                    choice.code,
                    order.order_number,
                )
            continue
        except Exception:
            # An opinion, or a bug. Either way the attempt is recorded before it
            # propagates, so a 500 in checkout is not also an invisible one.
            transaction.status = PaymentTransactionStatusEnum.FAILED.value
            transaction.error_code = "refused"
            await db.flush()
            raise

        transaction.session_id = session.session_id
        transaction.payment_id = session.payment_id
        transaction.checkout_url = session.checkout_url
        transaction.raw_status = session.raw_status

        order.payment_method = CARD
        order.payment_provider = choice.code
        order.payment_id = session.session_id
        await db.flush()

        logger.info(
            "Payment session created: order=%s gateway=%s session=%s%s",
            order.order_number,
            choice.code,
            session.session_id,
            f" (after {', '.join(tried[:-1])} failed)" if len(tried) > 1 else "",
        )

        return {
            "provider": choice.code,
            "session_id": session.session_id,
            "checkout_url": session.checkout_url,
        }

    logger.critical(
        "Every card gateway failed for order %s (tried %s) — card checkout is down",
        order.order_number,
        ", ".join(tried),
    )
    # The customer is told nothing about which processor failed or how. That
    # detail is in the CRITICAL above and on every abandoned transaction row,
    # which is where the person who can act on it is looking.
    raise BadRequestError(
        "Card payments are temporarily unavailable. Please try again shortly."
    )


async def handle_webhook(
    db: AsyncSession,
    gateway: str,
    payload: bytes,
    headers: Mapping[str, str],
) -> dict:
    """
    Verify and process a payment webhook from *gateway*.

    One function for every processor. The gateway's provider verifies the
    signature and translates the event; everything after that is written against
    `PaymentEventType` and has no idea who sent it.

    Dedup is handled atomically via INSERT ... ON CONFLICT DO NOTHING so that
    concurrent duplicate deliveries cannot cause double-processing. The row is
    written in the same transaction as the work, so a rolled-back attempt leaves
    nothing behind and a retry is free to try again.
    """
    provider = payment_gateway_router.PROVIDERS.get(gateway)
    if provider is None:
        raise NotFoundError(f"Unknown payment gateway '{gateway}'")

    event = provider.parse_webhook(payload, headers)

    if not event.event_id:
        # Without an ID there is no dedup, and without dedup a retry sends the
        # customer a second confirmation email. A gateway that issues none must
        # synthesise one; see `ziina_provider`.
        logger.error(
            "%s webhook had no event id (type=%s) — refusing to process it "
            "undeduplicated",
            gateway,
            event.raw_type,
        )
        raise BadRequestError("Webhook carried no event id")

    stmt = (
        pg_insert(WebhookEvent)
        .values(
            provider=gateway,
            event_id=event.event_id,
            event_type=event.raw_type[:100],
            order_number=event.order_number,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    insert_result = await db.execute(stmt)
    if insert_result.rowcount == 0:
        logger.info("Duplicate webhook skipped: event_id=%s", event.event_id)
        return {"received": True, "duplicate": True}

    await _apply_event(db, gateway, event)
    return {"received": True, "event_type": event.raw_type}


# ── Applying a normalised event ───────────────────────────────────────────────


async def _apply_event(db: AsyncSession, gateway: str, event: GatewayEvent) -> None:
    """Dispatch on what happened, not on who said it."""
    if event.event_type is PaymentEventType.UNHANDLED:
        logger.info(
            "%s webhook %s acknowledged and applied to nothing",
            gateway,
            event.raw_type,
        )
        return

    order = await _resolve_order(db, gateway, event)
    if order is None:
        return

    _record_transaction(order, gateway, event)

    if event.event_type is PaymentEventType.SUCCEEDED:
        await _handle_payment_succeeded(db, order, event)
    elif event.event_type is PaymentEventType.FAILED:
        await _handle_payment_failed(db, order, event)
    elif event.event_type is PaymentEventType.CANCELLED:
        logger.info(
            "Payment cancelled on %s for order %s — order left at %s",
            gateway,
            order.order_number,
            order.status,
        )
    elif event.event_type is PaymentEventType.REFUNDED:
        await _handle_refund(db, order, event)
    elif event.event_type is PaymentEventType.DISPUTED:
        await _handle_dispute(order, event)


async def _resolve_order(
    db: AsyncSession, gateway: str, event: GatewayEvent
) -> Order | None:
    """
    Which order this event is about, or a CRITICAL log and nothing.

    Never raises. An event we cannot place is money that moved with no order to
    attach it to, and the only useful response is to record it loudly and answer
    200 — raising would make the gateway retry a lookup that will fail
    identically forever, and on Ziina that is three retries and then silence.
    """
    try:
        if event.order_number:
            return await _load_order(db, event.order_number)
        return await _load_order_by_handle(
            db,
            gateway,
            payment_id=event.payment_id,
            session_id=event.session_id,
        )
    except NotFoundError:
        level = (
            logger.critical
            if event.event_type
            in (
                PaymentEventType.SUCCEEDED,
                PaymentEventType.REFUNDED,
                PaymentEventType.DISPUTED,
            )
            else logger.error
        )
        level(
            "%s %s — no order found (order_number=%s payment=%s session=%s) "
            "— manual reconciliation required",
            gateway,
            event.raw_type,
            event.order_number,
            event.payment_id,
            event.session_id,
        )
        return None


def _record_transaction(order: Order, gateway: str, event: GatewayEvent) -> None:
    """
    Bring the attempt's row in step with what the gateway just said.

    Matched on the handles rather than on "the most recent row", because a
    customer who abandons one checkout and starts another leaves two live
    attempts and the events for the first can arrive after the second exists.
    An event whose handles match nothing — which is every event on an order
    created before this table — is left alone rather than written onto the
    wrong row.
    """
    handles = {h for h in (event.payment_id, event.session_id) if h}
    if not handles:
        return

    status = _TRANSACTION_STATUSES.get(event.event_type)
    for transaction in order.payment_transactions:
        if transaction.gateway != gateway:
            continue
        if not ({transaction.payment_id, transaction.session_id} & handles):
            continue
        # Stripe's Payment Intent does not exist until the customer pays, so
        # this is where a Stripe attempt learns its own payment handle.
        if event.payment_id and not transaction.payment_id:
            transaction.payment_id = event.payment_id
        # A cancel or a failure arriving after a success is a late duplicate,
        # not a reversal — gateways reorder deliveries and Ziina emits a status
        # per transition with no ordering guarantee at all. Only a refund or a
        # dispute may move an attempt off `succeeded`, because only those two
        # actually undo it.
        if status is not None and not (
            transaction.is_settled
            and status
            not in (
                PaymentTransactionStatusEnum.REFUNDED,
                PaymentTransactionStatusEnum.DISPUTED,
            )
        ):
            transaction.status = status.value
        transaction.raw_status = event.raw_type[:60]
        if event.error_code:
            transaction.error_code = event.error_code[:80]
        if event.error_message:
            transaction.error_message = event.error_message[:2000]
        return


#: What each outcome means for the attempt's own row. `UNHANDLED` never gets
#: here, and `CANCELLED` deliberately does not clear a settled row — a cancel
#: arriving after a success is a late duplicate, not a reversal.
_TRANSACTION_STATUSES = {
    PaymentEventType.SUCCEEDED: PaymentTransactionStatusEnum.SUCCEEDED,
    PaymentEventType.FAILED: PaymentTransactionStatusEnum.FAILED,
    PaymentEventType.CANCELLED: PaymentTransactionStatusEnum.CANCELLED,
    PaymentEventType.REFUNDED: PaymentTransactionStatusEnum.REFUNDED,
    PaymentEventType.DISPUTED: PaymentTransactionStatusEnum.DISPUTED,
}


async def _handle_payment_succeeded(
    db: AsyncSession, order: Order, event: GatewayEvent
) -> None:
    payment_id = event.payment_id or event.session_id

    if order.status == OrderStatusEnum.CONFIRMED:
        if payment_id:
            order.payment_id = payment_id
        logger.info(
            "Payment succeeded webhook skipped — order %s is already confirmed",
            order.order_number,
        )
        return

    # An order that has already finished cannot be un-finished by a late
    # payment event. CANCELLED is the dangerous one: cancelling returns every
    # line's stock (order_service.update_status), so confirming it again would
    # put the order back into the kitchen holding no claim on ingredients that
    # may since have been sold twice. Gateways genuinely do deliver these —
    # a customer pays on a tab left open after the shop cancelled — so it is
    # recorded loudly for a human to refund rather than silently applied.
    if order.status not in _CONFIRMABLE_FROM:
        if payment_id:
            order.payment_id = payment_id
        logger.critical(
            "Payment succeeded for order %s in terminal status %s "
            "(gateway=%s payment=%s) — money was taken for an order that will "
            "not be fulfilled. Refund manually.",
            order.order_number,
            order.status,
            order.payment_provider,
            payment_id,
        )
        return

    if payment_id:
        order.payment_id = payment_id
    order.status = OrderStatusEnum.CONFIRMED
    order_response = await order_service.to_response(db, order)

    logger.info(
        "Payment confirmed: order=%s gateway=%s payment=%s",
        order.order_number,
        order.payment_provider,
        payment_id,
    )

    try:
        await email_service.send_order_confirmation(order_response)
        await email_service.send_owner_order_notification(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send order confirmation emails for %s: %s",
            order.order_number,
            exc,
        )


async def _handle_payment_failed(
    db: AsyncSession, order: Order, event: GatewayEvent
) -> None:
    if order.status != OrderStatusEnum.CREATED:
        logger.info(
            "Payment failed event ignored — order %s already in status %s",
            order.order_number,
            order.status,
        )
        return

    order.status = OrderStatusEnum.PAYMENT_FAILED
    order_response = await order_service.to_response(db, order)

    logger.warning(
        "Payment failed: gateway=%s event=%s order=%s reason=%s",
        order.payment_provider,
        event.raw_type,
        order.order_number,
        event.error_message or event.error_code or "unstated",
    )

    try:
        await email_service.send_payment_failed(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send payment failed email for %s: %s",
            order.order_number,
            exc,
        )


async def _handle_refund(db: AsyncSession, order: Order, event: GatewayEvent) -> None:
    """
    A refund is not automatically the whole order.

    Stripe fires `charge.refunded` for a partial too. This used to move the
    order to REFUNDED either way, so knocking AED 5 off a damaged box marked the
    whole order refunded, dropped it out of the fulfilment views and told the
    customer by email that their money was on its way back. A partial is now
    recorded and reported, and the order keeps the status it had.
    """
    is_full = _is_full_refund(order, event)

    if not is_full:
        logger.warning(
            "Partial refund on order %s (gateway=%s payment=%s): %s of %s minor "
            "units refunded. Order status left at %s — adjust by hand if the "
            "whole order is being unwound.",
            order.order_number,
            order.payment_provider,
            event.payment_id,
            event.amount_refunded,
            event.amount_captured,
            order.status,
        )
        return

    if order.status == OrderStatusEnum.REFUNDED:
        logger.info(
            "Refund webhook skipped — order %s is already refunded",
            order.order_number,
        )
        return

    order.status = OrderStatusEnum.REFUNDED
    order_response = await order_service.to_response(db, order)

    logger.info(
        "Refund processed: order=%s gateway=%s payment=%s",
        order.order_number,
        order.payment_provider,
        event.payment_id,
    )

    try:
        await email_service.send_refund_notification(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send refund notification for %s: %s",
            order.order_number,
            exc,
        )


def _is_full_refund(order: Order, event: GatewayEvent) -> bool:
    """
    Whether this refund unwound the entire charge.

    The gateway's own "nothing left on this charge" flag is authoritative when
    it sends one — Stripe does. The amounts are the fallback, and the second
    fallback is the order's own total, which is what answers it for Ziina: their
    refund object carries what was refunded and never what was originally
    captured, so the only thing to compare against is what we charged.

    When none of the three is readable the answer is "yes". Treating an unknown
    as full preserves the behaviour every refund had before partials were told
    apart, so an unrecognised payload cannot quietly leave a fully-refunded
    order looking unrefunded.
    """
    if event.fully_refunded is not None:
        return bool(event.fully_refunded)
    if event.amount_refunded is not None and event.amount_captured:
        return event.amount_refunded >= event.amount_captured
    if event.amount_refunded is not None and order.total is not None:
        return event.amount_refunded >= int(Decimal(str(order.total)) * 100)
    return True


async def _handle_dispute(order: Order, event: GatewayEvent) -> None:
    """
    Chargeback filed — mark order as DISPUTED and log CRITICAL so it surfaces
    in monitoring. No customer email — this requires manual admin review.
    """
    order.status = OrderStatusEnum.DISPUTED
    logger.critical(
        "CHARGEBACK FILED: order=%s gateway=%s payment=%s "
        "— immediate manual review required",
        order.order_number,
        order.payment_provider,
        event.payment_id,
    )


async def get_status(
    db: AsyncSession,
    order_number: str,
    *,
    user_id: uuid.UUID | None = None,
    admin: bool = False,
) -> dict:
    """Return payment status for an order."""
    order = await _load_order(db, order_number)
    _assert_may_act_on(order, user_id, admin)

    return {
        "order_number": order.order_number,
        "payment_provider": order.payment_provider,
        "payment_method": order.payment_method,
        "payment_id": order.payment_id,
        "paid": _is_paid(order),
        "order_status": order.status,
    }
