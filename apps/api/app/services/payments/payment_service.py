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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.base import utcnow
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.payment_transaction import (
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from app.models.pos_order import OrderSourceEnum
from app.models.webhook_event import WebhookEvent
from app.services import email_service
from app.services.orders import order_lifecycle, order_service
from app.services.payments import payment_gateway_router
from app.services.payments.payment_methods import CARD, COD, normalise_method
from app.services.providers.base import (
    GatewayEvent,
    GatewayUnavailableError,
    PaymentEventType,
)
from app.services.providers.stripe_provider import provider as stripe_provider

__all__ = [
    "COD",
    "create_session",
    "expire_stale_checkouts",
    "get_status",
    "handle_webhook",
    "normalise_method",
]

logger = logging.getLogger(__name__)

#: The money endings a gateway may report on an order the map never expected
#: there. A dashboard refund can land on an order that never confirmed, and a
#: chargeback can arrive after anything. The gateway's ledger is the authority
#: on where the money went, so these widen where a `refunded`/`disputed` move
#: may *start* — never where an order may otherwise go.
_MONEY_FACTS_FROM = {
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

    # Scoped to the gateway that sent the event, exactly like the query above.
    # Both processors mint ids beginning `pi_`, and an unscoped match here would
    # let a Ziina event land on a Stripe order that happened to share a handle —
    # confirming an order nobody paid for and mailing the customer about it.
    # Vanishingly unlikely, and not a coin worth flipping on a code path whose
    # failure mode is "the shop bakes a cake".
    #
    # A null provider counts as Stripe, and only as Stripe: it is the one
    # gateway that existed when the rows carrying a bare `pi_…` were written.
    owner = Order.payment_provider == gateway
    if gateway == "stripe":
        owner = owner | Order.payment_provider.is_(None)

    legacy = (
        (
            await db.execute(
                select(Order)
                .options(
                    selectinload(Order.items), selectinload(Order.payment_transactions)
                )
                .where(Order.payment_id.in_(handles), owner)
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
        await order_lifecycle.transition(db, order, OrderStatusEnum.CREATED)
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
        # `transition` publishes to the register on the way through.
        await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)
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
        if not already_confirmed:
            # Confirms and publishes in one move. An order that has gone past
            # `confirmed` — packed, out the door — is not a thing a payment
            # session may walk backwards, and `transition` now says so instead
            # of silently rewriting it.
            await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)
        order.payment_method = COD
        order.payment_provider = COD
        order.payment_id = None
        if already_confirmed:
            # The usual path has already published, and `publish_to_register`
            # no-ops on a second call — but a cash order that reached
            # `confirmed` without a check number still has to reach a counter.
            await order_service.publish_to_register(db, order)
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
        # nothing available — rather than a generic failure. `select_gateway`
        # always raises on an empty list; the raise after it is here so that a
        # future edit which makes it return instead fails loudly rather than
        # falling into `options[0]` and an IndexError inside a checkout.
        await payment_gateway_router.select_gateway(db, order_total)
        raise BadRequestError(
            "Card payments are temporarily unavailable. Please try again shortly."
        )

    choice = options[0]
    tried: list[str] = []

    while choice is not None:
        tried.append(choice.code)

        try:
            session = await choice.provider.create_session(
                order, test_mode=choice.test_mode
            )
        except GatewayUnavailableError as exc:
            _record_failed_attempt(
                db, order, choice.code, order_total, "gateway_unavailable", str(exc)
            )
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
        except Exception as exc:
            # An opinion about this order, or a bug. Either way it propagates,
            # and `get_db` rolls the whole request back on the way out — so the
            # row added here does not survive, and is not meant to. Nothing
            # happened, and the database should say nothing happened. The
            # diagnosis lives in the log line, which is not transactional.
            _record_failed_attempt(
                db, order, choice.code, order_total, "refused", str(exc)
            )
            logger.error(
                "Gateway '%s' refused a session for %s: %s",
                choice.code,
                order.order_number,
                exc,
            )
            raise

        transaction = _attempt_for(order, choice.code, session.session_id)
        if transaction is None:
            transaction = PaymentTransaction(
                order_id=order.id,
                gateway=choice.code,
                amount=order_total,
                currency="AED",
            )
            order.payment_transactions.append(transaction)
            db.add(transaction)

        transaction.status = PaymentTransactionStatusEnum.PENDING.value
        transaction.session_id = session.session_id
        transaction.payment_id = session.payment_id
        transaction.checkout_url = session.checkout_url
        transaction.raw_status = session.raw_status
        transaction.amount = order_total

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


def _attempt_for(
    order: Order, gateway: str, session_id: str | None
) -> PaymentTransaction | None:
    """
    The existing attempt this session belongs to, if there is one.

    **This is what stops a payment retry 500ing.** Stripe is called with
    `idempotency_key=f"sess_{order_number}"`, so a second `create_session` for
    the same order inside 24 hours returns the *same* Checkout Session — same
    `cs_…` id. Inserting a new row for it violates
    `uq_payment_transactions_gateway_session`, and the customer whose card was
    declined and who pressed "try again" gets a 500 instead of a payment page.

    Reusing the row is also the truthful reading: Stripe handed back the same
    session because it *is* the same attempt, not a new one. A retry that
    genuinely produces a new session — every Ziina one, since they take no
    idempotency key — finds nothing here and gets its own row.
    """
    if not session_id:
        return None
    for transaction in order.payment_transactions:
        if transaction.gateway == gateway and transaction.session_id == session_id:
            return transaction
    return None


def _record_failed_attempt(
    db: AsyncSession,
    order: Order,
    gateway: str,
    amount: Decimal,
    error_code: str,
    message: str,
) -> None:
    """
    Note an attempt that never produced a session.

    No `session_id`, which is what keeps it clear of the unique index — several
    of these can exist for one order, and on a bad day they should.

    It survives only if the request does: a failover that eventually finds a
    working gateway commits the whole set, so "Stripe was tried first and 502'd"
    is on the order. A request that ends in an exception rolls back and takes
    these with it, which is correct — nothing happened, and the database should
    not claim otherwise. `logger.error` above is the non-transactional record.
    """
    db.add(
        PaymentTransaction(
            order_id=order.id,
            gateway=gateway,
            status=PaymentTransactionStatusEnum.FAILED.value,
            amount=amount,
            currency="AED",
            error_code=error_code,
            error_message=message[:2000],
        )
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

    The returned dict is both the HTTP body and what the route records in
    `webhook_logs`. `matched` is present only once a lookup was actually
    attempted — see `_apply_event` — because a null there means "the question
    did not arise" and a `false` means "we should have found an order and did
    not", and only the second is worth waking up for.
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
    #: What every answer carries, so a log row is identifiable even when the
    #: event was applied to nothing.
    described = {
        "received": True,
        "event_type": event.raw_type,
        "external_id": event.payment_id or event.session_id,
    }

    insert_result = await db.execute(stmt)
    if insert_result.rowcount == 0:
        logger.info("Duplicate webhook skipped: event_id=%s", event.event_id)
        return {**described, "duplicate": True}

    outcome = await _apply_event(db, gateway, event)
    return {**described, **outcome}


# ── Applying a normalised event ───────────────────────────────────────────────


async def _apply_event(db: AsyncSession, gateway: str, event: GatewayEvent) -> dict:
    """
    Dispatch on what happened, not on who said it.

    Returns what the audit log needs to know about the attempt: whether an order
    was found, and which one. `matched` is deliberately absent from the answer
    when no lookup happened, rather than `false` — see `handle_webhook`.
    """
    if event.event_type is PaymentEventType.UNHANDLED:
        logger.info(
            "%s webhook %s acknowledged and applied to nothing",
            gateway,
            event.raw_type,
        )
        return {"applied": False}

    order = await _resolve_order(db, gateway, event)
    if order is None:
        return {"applied": False, "matched": False}

    _record_transaction(order, gateway, event)

    # Four of the five branches below move the order's status, and this is the
    # one place all of them pass through. Attributing here rather than at each
    # assignment means a fifth branch is attributed correctly the day it is
    # written, instead of quietly recording itself as `system`.
    with acting_as(
        StatusSourceEnum.PAYMENT.value,
        actor_label=gateway,
        note=event.raw_type,
    ):
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
        elif event.event_type is PaymentEventType.EXPIRED:
            await _handle_checkout_expired(db, order)
        elif event.event_type is PaymentEventType.REFUNDED:
            await _handle_refund(db, order, event)
        elif event.event_type is PaymentEventType.DISPUTED:
            await _handle_dispute(db, order, event)

    return {
        "applied": True,
        "matched": True,
        "order_number": order.order_number,
    }


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
        if event.failure_reason is not None:
            transaction.failure_reason = event.failure_reason.value
        return


#: What each outcome means for the attempt's own row. `UNHANDLED` never gets
#: here, and `CANCELLED` deliberately does not clear a settled row — a cancel
#: arriving after a success is a late duplicate, not a reversal.
_TRANSACTION_STATUSES = {
    PaymentEventType.SUCCEEDED: PaymentTransactionStatusEnum.SUCCEEDED,
    PaymentEventType.FAILED: PaymentTransactionStatusEnum.FAILED,
    PaymentEventType.CANCELLED: PaymentTransactionStatusEnum.CANCELLED,
    # No status of its own on the attempt: `CANCELLED` is already spelled "the
    # customer walked away", which is exactly what an expiry is. A new column
    # value would split one fact across two words for no reader's benefit —
    # `raw_status` keeps `checkout.session.expired` for anyone who needs to know
    # which way the walking-away was noticed.
    PaymentEventType.EXPIRED: PaymentTransactionStatusEnum.CANCELLED,
    PaymentEventType.REFUNDED: PaymentTransactionStatusEnum.REFUNDED,
    PaymentEventType.DISPUTED: PaymentTransactionStatusEnum.DISPUTED,
}


#: Statuses a "payment succeeded" push has nothing left to do for. Not the same
#: set as "cannot reach `confirmed`" — an order further down the chain is
#: fulfilled rather than terminal, and telling a human to refund it would be
#: wrong in the loudest available way.
_ALREADY_PAID_FOR = frozenset(
    {
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
        OrderStatusEnum.PACKED,
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
    }
)


async def _handle_payment_succeeded(
    db: AsyncSession, order: Order, event: GatewayEvent
) -> None:
    payment_id = event.payment_id or event.session_id

    # Already paid for and already moving. `arrived_at_pos` belongs here as much
    # as `confirmed` does: confirmation now lands the arrival in the same act
    # for every zone without a shared run, so by the time a gateway's retry
    # arrives — and they all retry — the order has usually gone one further.
    # Reading only `confirmed` sent those down the terminal-status branch below
    # and logged a critical about money taken for an order that will not be
    # fulfilled, for the entirely ordinary event of a webhook being delivered
    # twice.
    if order.status in _ALREADY_PAID_FOR:
        if payment_id:
            order.payment_id = payment_id
        logger.info(
            "Payment succeeded webhook skipped — order %s is already %s",
            order.order_number,
            order.status.value,
        )
        return

    # An order that has already finished cannot be un-finished by a late
    # payment event. CANCELLED is the dangerous one: cancelling returns every
    # line's stock (order_service.update_status), so confirming it again would
    # put the order back into the kitchen holding no claim on ingredients that
    # may since have been sold twice. Gateways genuinely do deliver these —
    # a customer pays on a tab left open after the shop cancelled — so it is
    # recorded loudly for a human to refund rather than silently applied.
    if not order_lifecycle.can_transition(order.status, OrderStatusEnum.CONFIRMED):
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
    # The money has landed, so now the order becomes work: `transition` schedules
    # its arrival on the way through, and lands it there and then unless it is
    # waiting for a shared run. Before this the customer had only opened a
    # payment page, and a kitchen already shouted at about it would be baking
    # against a charge that might never be made.
    await order_lifecycle.transition(db, order, OrderStatusEnum.CONFIRMED)

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


async def _handle_checkout_expired(db: AsyncSession, order: Order) -> bool:
    """
    A checkout nobody finished, closed out. Returns whether the order moved.

    **Cancelled rather than `payment_failed`.** Nothing was refused here: the
    gateway never attempted a charge, the customer simply left the page. Calling
    that a payment failure would put a card-declined story on an order that has
    none, and would send the customer the payment-failed email for a checkout
    they walked away from on purpose.

    Left at `created`, which is what happened until now, it is not harmless. The
    row keeps whatever the checkout claimed when it was written: a redemption of
    its promo code, a place in the customer's first-orders count, and — for a
    stock product — stock taken off the shelf at checkout and never put back.
    `_redemptions_by` and `orders_placed_by` both exclude *cancelled* orders and
    nothing else, so an abandoned checkout quietly spends a coupon the customer
    still has. MM-20260820-001 did exactly that: opened at 05:16, abandoned,
    re-ordered as -002 at 05:28, and the first one went on holding a use of the
    NEW code for a sale that never existed.

    Cancelling gives all of that back through `_consequences`, which restocks
    and releases, and it sends nothing to anybody: the cancellation email is
    dispatched by its callers, not by `transition`, and an order the customer
    abandoned themselves does not need to be told about.

    Guarded twice on purpose. `created` is the only status this may touch — an
    expiry arriving after a successful payment is a late duplicate, not a
    reversal — and `_is_paid` is asked as well, because the status is our record
    of the money and this is precisely the case where our record could be the
    thing that is wrong. A wrongly cancelled order cannot be walked back:
    `cancelled` is terminal outside `ADMIN_RECOVERABLE`.
    """
    if order.status != OrderStatusEnum.CREATED:
        logger.info(
            "Checkout expiry ignored — order %s is already %s",
            order.order_number,
            getattr(order.status, "value", order.status),
        )
        return False

    if _is_paid(order):
        # Paid, and sitting at `created` anyway. That is a webhook we missed,
        # not an abandoned basket, and cancelling it would restock a box
        # somebody bought. Loud, because it means the confirmation never ran.
        logger.critical(
            "Order %s expired at the gateway but reads as paid — "
            "it needs reconciling by hand, not cancelling",
            order.order_number,
        )
        return False

    moved = await order_lifecycle.transition(
        db, order, OrderStatusEnum.CANCELLED, on_invalid="skip"
    )
    if moved:
        logger.info(
            "Order %s cancelled: its checkout expired without a payment",
            order.order_number,
        )
    return moved


#: How long an unpaid checkout is left alone before it is closed out without
#: the gateway's say-so. Stripe gives a Checkout Session 24 hours and then fires
#: `checkout.session.expired` itself, so this is deliberately well past that:
#: anything still `created` two days later is an event that never arrived — a
#: webhook dropped, a signature secret rotated mid-flight, a deploy that was
#: down for the one delivery Stripe made — rather than a customer still
#: deciding. Being late here costs a coupon use sitting idle for an extra day.
#: Being early would cancel a checkout somebody is still paying for.
_ABANDONED_AFTER = timedelta(hours=48)


async def expire_stale_checkouts(db: AsyncSession, *, limit: int = 100) -> list[str]:
    """
    Cancel checkouts nobody finished and nobody told us about. Returns what moved.

    The backstop under `checkout.session.expired`, not a replacement for it. The
    webhook is the timely answer and arrives with the gateway's own word for
    what happened; this is what covers the delivery that never landed, and the
    orders written before the event was mapped at all.

    Its whole safety lives in `_handle_checkout_expired`, which refuses anything
    that is not still `created` and anything that reads as paid — and that is
    why the transactions are eager-loaded here rather than left to a lazy
    fetch that would raise inside async SQLAlchemy: the paid check is the one
    that must never be skipped, and `items` is what the restock walks.

    Sequential, and small. A hundred a tick is far more than a shop this size
    abandons in a day, and one order failing must not take the rest with it.
    """
    now = datetime.now(timezone.utc)
    stale = (
        (
            await db.execute(
                select(Order)
                .options(
                    selectinload(Order.items),
                    selectinload(Order.payment_transactions),
                )
                .where(
                    Order.status == OrderStatusEnum.CREATED,
                    # A counter sale is never in this state waiting for a card
                    # page, and a cashier's open check is not this function's
                    # business.
                    Order.source == OrderSourceEnum.ONLINE.value,
                    Order.created_at <= now - _ABANDONED_AFTER,
                )
                .order_by(Order.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    cancelled: list[str] = []
    for order in stale:
        # Ours, not the gateway's: no webhook said this, we decided it from a
        # clock. `acting_as` puts that in the status history rather than
        # attributing the cancellation to Stripe.
        with acting_as(
            StatusSourceEnum.SYSTEM.value,
            note="checkout abandoned without payment",
        ):
            if await _handle_checkout_expired(db, order):
                cancelled.append(order.order_number)
    return cancelled


async def _handle_payment_failed(
    db: AsyncSession, order: Order, event: GatewayEvent
) -> None:
    if not await order_lifecycle.transition(
        db, order, OrderStatusEnum.PAYMENT_FAILED, on_invalid="skip"
    ):
        logger.info(
            "Payment failed event ignored — order %s already in status %s",
            order.order_number,
            order.status,
        )
        return

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
    # Whatever else this webhook means, it means money went back — and the
    # gateway's figure is the authority on how much, whoever issued it.
    #
    # Written for partials too, which is the case this used to lose entirely: a
    # refund made by hand in a Stripe dashboard moved the status and left
    # `refunded_amount` at zero, so the admin screen and the customer's email
    # both said nothing had been returned while the bank disagreed. It is also
    # what stops `refund_order` sending a second refund on top of a dashboard
    # one — `refundable_amount` subtracts it.
    if event.amount_refunded is not None:
        order.refunded_amount = Decimal(event.amount_refunded) / 100
        order.refunded_at = order.refunded_at or utcnow()

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

    # `disputed` is in the widening too: a refund issued to settle a
    # chargeback still means the money went back, and that is the fact the
    # order should end on.
    await order_lifecycle.transition(
        db,
        order,
        OrderStatusEnum.REFUNDED,
        extra_from=_MONEY_FACTS_FROM | {OrderStatusEnum.DISPUTED},
        on_invalid="skip",
    )
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


async def _handle_dispute(db: AsyncSession, order: Order, event: GatewayEvent) -> None:
    """
    Chargeback filed — mark order as DISPUTED and log CRITICAL so it surfaces
    in monitoring. No customer email — this requires manual admin review.

    A chargeback can arrive after anything, including a refund the map calls
    terminal — the bank does not consult our state machine — so the widening
    covers every status a dispute has ever landed on.
    """
    await order_lifecycle.transition(
        db,
        order,
        OrderStatusEnum.DISPUTED,
        extra_from=_MONEY_FACTS_FROM | {OrderStatusEnum.REFUNDED},
        on_invalid="skip",
    )
    logger.critical(
        "CHARGEBACK FILED: order=%s gateway=%s payment=%s "
        "— immediate manual review required",
        order.order_number,
        order.payment_provider,
        event.payment_id,
    )


def refundable_amount(order: Order) -> Decimal:
    """
    What the customer gets back: what they bought, and none of the fees.

    The shop's rule, and it is a commercial one rather than an arithmetic one.
    The delivery fee bought a van that was booked and — on an undelivered order —
    usually already drove; the low-order fee bought the same thing. Handing
    those back means paying twice for a journey that happened. So the refund is
    the goods at the price actually charged for them: subtotal less whatever
    discount was applied, which is exactly `total` minus the fees.

    Reads `total` rather than recomputing from `subtotal - discount`, because
    `total` is what the card was charged and any drift between the two would
    show up as a refund the customer disputes. Whatever is left after taking the
    fees off it is, by construction, the money that bought cake.

    Never more than is left to refund, so a second call after a partial refund
    made in a dashboard cannot overdraw the charge.
    """
    total = Decimal(str(order.total or 0))
    fees = Decimal(str(order.delivery_fee or 0)) + Decimal(
        str(order.low_order_fee or 0)
    )
    goods = max(total - fees, Decimal("0"))
    already = Decimal(str(order.refunded_amount or 0))
    return max(min(goods, total - already), Decimal("0"))


def _refund_key(order: Order, amount: Decimal) -> str:
    """
    A stable name for "this refund", so asking twice is asking once.

    Derived from the order and the amount rather than randomly, because the
    whole point is that two independent callers — an admin click and the retry
    behind it, a webhook and a status transition — produce the *same* key and
    therefore the same refund. A random key would make each of them a new one.

    Both gateways accept it: Stripe as its idempotency header, Ziina as the
    refund's own client-generated primary key.
    """
    return f"refund-{order.order_number}-{amount:.2f}"


async def refund_order(
    db: AsyncSession,
    order: Order,
    *,
    amount: Decimal | None = None,
) -> Decimal:
    """
    Send money back for an order that is not going to arrive.

    Returns what was refunded, or zero when there was nothing to do — which is
    the ordinary answer for a cash order, an unpaid one, or one already
    refunded, and is not a failure in any of those cases.

    **Called automatically when an order is cancelled or marked undelivered.**
    That is the shop's decision and it is the right one for a business that
    takes payment up front: the alternative is a second manual step that gets
    forgotten, and the failure mode of forgetting is a customer who paid for a
    cake they never got. The cost of the decision is that money moves on a
    status change, which is why the idempotency below is not optional.

    Deliberately quiet on failure. A gateway that is unreachable must not
    prevent an order being cancelled — the cancellation is a fact about the
    kitchen, the refund is a fact about a bank, and holding the first hostage to
    the second leaves the shop unable to stop making a cake. The failure is
    logged and the order carries `refunded_amount = 0`, which is what the
    admin's refund list reads.
    """
    if order.payment_method != CARD:
        return Decimal("0")

    attempt = await _settled_attempt(db, order)
    if attempt is None or not attempt.payment_id:
        # Nothing was ever charged through a gateway we can call back. A cash
        # order, or a card attempt that never reached a payment handle.
        return Decimal("0")
    if attempt.refund_id:
        # Already refunded through here. The amount is on the order.
        return Decimal("0")

    payable = refundable_amount(order)
    requested = payable if amount is None else min(amount, payable)
    if requested <= 0:
        return Decimal("0")

    gateway = payment_gateway_router.PROVIDERS.get(attempt.gateway)
    if gateway is None:
        logger.error(
            "Order %s was paid on %s, which this build cannot refund",
            order.order_number,
            attempt.gateway,
        )
        return Decimal("0")

    # ── The retry-safety invariant. Read before touching anything above. ──
    #
    # This gateway call happens *inside* the caller's open transaction, and the
    # writes below it (`refunded_amount`, `refund_id`) are only flushed — they
    # commit with the request. So the dangerous window is real: the gateway can
    # move the money and the transaction can then roll back, leaving the
    # database with no record that it did.
    #
    # What makes that window survivable is that a retry reconstructs the *same*
    # request bit-for-bit, so the gateway's idempotency dedupes it:
    #
    #   * `refundable_amount` reads only columns the rollback restored —
    #     `total`, the two fees, `refunded_amount` — so `requested` recomputes
    #     to the identical figure. This is the load-bearing part: the very
    #     rollback that lost our record also rewound every input to the key.
    #   * `_refund_key` derives from order number + that amount and nothing
    #     else. No timestamp, no uuid4, no attempt counter.
    #
    # Recording the attempt durably *before* the call — the properly designed
    # outbox shape — needs somewhere to put it, and neither `order_payments`
    # nor anything else has a pending-refund column today; inventing one is a
    # migration this fix deliberately avoids. Until that exists, the invariant
    # above is the safety. Breaking it looks innocent: seeding the key with
    # anything non-deterministic, mutating `refunded_amount` before this call,
    # or letting `requested` depend on state this same transaction changed
    # would each turn a retried rollback into a second, real refund.
    try:
        result = await gateway.refund(
            payment_id=attempt.payment_id,
            amount=requested,
            idempotency_key=_refund_key(order, requested),
        )
    except Exception:  # noqa: BLE001 — see the docstring
        logger.exception(
            "Could not refund %s for order %s; it needs a person",
            requested,
            order.order_number,
        )
        return Decimal("0")

    order.refunded_amount = Decimal(str(order.refunded_amount or 0)) + result.amount
    order.refunded_at = order.refunded_at or utcnow()
    attempt.refund_id = result.refund_id
    logger.info(
        "Refunded %s %s for %s (%s, %s)",
        result.amount,
        order.currency or "AED",
        order.order_number,
        result.refund_id,
        result.status,
    )
    return result.amount


async def _settled_attempt(db: AsyncSession, order: Order) -> PaymentTransaction | None:
    """
    The attempt that actually took the money.

    `payment_transactions` holds one row per try, and a customer who abandoned a
    card screen twice before paying leaves three. Only the settled one has a
    handle worth refunding against.

    **Queried rather than read off the relationship.** This is reached from
    `order_lifecycle._consequences`, which every cancellation goes through —
    the admin console, a courier webhook, a register, the checkout. Only one of
    those could ever be persuaded to eager-load the collection, and under
    asyncpg touching an unloaded one is a `MissingGreenlet` rather than a lazy
    query. So the admin console answered 500 to every attempt to cancel a card
    order, and the traceback named a refund helper rather than the cancellation
    the person had asked for.
    """
    rows = (
        (
            await db.execute(
                select(PaymentTransaction).where(
                    PaymentTransaction.order_id == order.id
                )
            )
        )
        .scalars()
        .all()
    )
    for attempt in rows:
        if attempt.is_settled and attempt.payment_id:
            return attempt
    return None


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
