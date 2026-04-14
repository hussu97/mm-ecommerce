from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.order import Order, OrderStatusEnum
from app.models.webhook_event import WebhookEvent
from app.schemas.order import OrderResponse
from app.services import email_service
from app.services.providers.base import PaymentProvider
from app.services.providers.stripe_provider import provider as stripe_provider

__all__ = [
    "create_session",
    "get_status",
    "handle_stripe_webhook",
    "handle_tabby_webhook",
    "handle_tamara_webhook",
]

logger = logging.getLogger(__name__)

# Registry: only providers that are fully implemented.
# Tabby and Tamara are stubs — add them here once integrated.
_PROVIDERS: dict[str, PaymentProvider] = {
    "stripe": stripe_provider,
}


def _get_provider(name: str) -> PaymentProvider:
    p = _PROVIDERS.get(name.lower())
    if not p:
        raise BadRequestError(
            f"Unknown payment provider '{name}'. Currently supported: "
            + ", ".join(_PROVIDERS)
        )
    return p


async def _load_order(db: AsyncSession, order_number: str) -> Order:
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.order_number == order_number)
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(f"Order '{order_number}' not found")
    return order


async def _load_order_by_payment_intent(
    db: AsyncSession, payment_intent_id: str | None
) -> Order:
    """Look up an order by its confirmed payment_intent ID (pi_...)."""
    if not payment_intent_id:
        raise NotFoundError("No payment_intent_id provided")
    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.payment_id == payment_intent_id)
    )
    result = await db.execute(stmt)
    order = result.scalar_one_or_none()
    if not order:
        raise NotFoundError(f"No order found for payment_intent '{payment_intent_id}'")
    return order


async def create_session(db: AsyncSession, order_number: str, provider: str) -> dict:
    """
    Create a payment checkout session for the given order.
    Stores session_id in order.payment_id. Returns {provider, session_id, checkout_url}.
    """
    order = await _load_order(db, order_number)

    if order.status == OrderStatusEnum.CANCELLED:
        raise BadRequestError("Cannot create payment session for a cancelled order")

    # Allow retry: reset payment_failed orders back to created
    if order.status == OrderStatusEnum.PAYMENT_FAILED:
        order.status = OrderStatusEnum.CREATED
        await db.flush()

    # Idempotency: if already has a confirmed payment, reject
    if stripe_provider.is_confirmed_payment_id(order.payment_id):
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
        order_response = OrderResponse.model_validate(order)
        await email_service.send_order_confirmation(order_response)
        return {
            "provider": "none",
            "session_id": None,
            "checkout_url": None,
            "confirmed": True,
        }

    # Stripe AED minimum is 2.00 AED. Orders below this threshold cannot be charged.
    _STRIPE_AED_MINIMUM = Decimal("2.00")
    if provider == "stripe" and order_total < _STRIPE_AED_MINIMUM:
        raise BadRequestError(
            f"The order total after discount (AED {order_total:.2f}) is below the minimum "
            f"chargeable amount of AED {_STRIPE_AED_MINIMUM:.2f}. "
            "Please add more items or adjust your discount."
        )

    p = _get_provider(provider)
    result = p.create_session(order)

    # Persist session_id + provider on the order
    order.payment_provider = provider
    order.payment_id = result["session_id"]
    await db.flush()

    logger.info(
        "Payment session created: order=%s provider=%s session=%s",
        order_number,
        provider,
        result["session_id"],
    )

    return {
        "provider": provider,
        "session_id": result["session_id"],
        "checkout_url": result["checkout_url"],
    }


async def handle_stripe_webhook(
    db: AsyncSession, payload: bytes, signature: str
) -> dict:
    """
    Verify and process a Stripe webhook event.

    Handles:
      - payment_intent.succeeded     → confirm order, send confirmation email
      - payment_intent.payment_failed → mark order failed, send failure email
      - charge.refunded               → mark order refunded, send refund email
      - charge.dispute.created        → mark order disputed, log CRITICAL for admin

    Dedup is handled atomically via INSERT ... ON CONFLICT DO NOTHING so that
    concurrent duplicate deliveries from Stripe cannot cause double-processing.
    """
    parsed = stripe_provider.handle_webhook(payload, signature)
    event_id: str = parsed["event_id"]
    event_type: str = parsed["event_type"]
    order_number: str | None = parsed.get("order_number")
    payment_intent_id: str | None = parsed.get("payment_intent_id")

    # Atomic dedup: if this event_id was already processed the INSERT is a no-op
    # (the unique index on event_id is enforced at the DB level — race-condition-safe)
    stmt = (
        pg_insert(WebhookEvent)
        .values(
            provider="stripe",
            event_id=event_id,
            event_type=event_type,
            order_number=order_number,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    insert_result = await db.execute(stmt)
    if insert_result.rowcount == 0:
        logger.info("Duplicate webhook skipped: event_id=%s", event_id)
        return {"received": True, "duplicate": True}

    if event_type == "payment_intent.succeeded":
        await _handle_payment_succeeded(db, order_number, payment_intent_id)

    elif event_type == "payment_intent.payment_failed":
        await _handle_payment_failed(db, order_number, event_type)

    elif event_type == "charge.refunded":
        await _handle_charge_refunded(db, order_number, payment_intent_id)

    elif event_type == "charge.dispute.created":
        await _handle_dispute_created(db, payment_intent_id)

    return {"received": True, "event_type": event_type}


# ── Private handlers ──────────────────────────────────────────────────────────


async def _handle_payment_succeeded(
    db: AsyncSession,
    order_number: str | None,
    payment_intent_id: str | None,
) -> None:
    if not order_number:
        logger.critical(
            "payment_intent.succeeded — no order_number in metadata "
            "(payment_intent=%s) — manual reconciliation required",
            payment_intent_id,
        )
        return

    try:
        order = await _load_order(db, order_number)
    except NotFoundError:
        logger.critical(
            "payment_intent.succeeded — order not found for order_number=%s "
            "(payment_intent=%s) — manual reconciliation required",
            order_number,
            payment_intent_id,
        )
        return

    if payment_intent_id:
        order.payment_id = payment_intent_id
    order.status = OrderStatusEnum.CONFIRMED
    order_response = OrderResponse.model_validate(order)

    logger.info(
        "Payment confirmed: order=%s payment_intent=%s",
        order_number,
        payment_intent_id,
    )

    try:
        await email_service.send_order_confirmation(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send order confirmation email for %s: %s",
            order_number,
            exc,
        )


async def _handle_payment_failed(
    db: AsyncSession,
    order_number: str | None,
    event_type: str,
) -> None:
    if not order_number:
        logger.warning(
            "%s — no order_number in metadata, skipping status update",
            event_type,
        )
        return

    try:
        order = await _load_order(db, order_number)
    except NotFoundError:
        logger.error(
            "Webhook: order not found for order_number=%s (event=%s)",
            order_number,
            event_type,
        )
        return

    if order.status != OrderStatusEnum.CREATED:
        logger.info(
            "Payment failed event ignored — order %s already in status %s",
            order_number,
            order.status,
        )
        return

    order.status = OrderStatusEnum.PAYMENT_FAILED
    order_response = OrderResponse.model_validate(order)

    logger.warning(
        "Payment failed: event=%s order=%s",
        event_type,
        order_number,
    )

    try:
        await email_service.send_payment_failed(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send payment failed email for %s: %s",
            order_number,
            exc,
        )


async def _handle_charge_refunded(
    db: AsyncSession,
    order_number: str | None,
    payment_intent_id: str | None,
) -> None:
    """
    Charge metadata doesn't always carry order_number — fall back to looking
    up the order by payment_intent_id (stored in order.payment_id after confirmation).
    """
    try:
        order = (
            await _load_order(db, order_number)
            if order_number
            else await _load_order_by_payment_intent(db, payment_intent_id)
        )
    except NotFoundError:
        logger.critical(
            "charge.refunded — no order found for order_number=%s / "
            "payment_intent=%s — manual reconciliation required",
            order_number,
            payment_intent_id,
        )
        return

    order.status = OrderStatusEnum.REFUNDED
    order_response = OrderResponse.model_validate(order)

    logger.info(
        "Refund processed: order=%s payment_intent=%s",
        order.order_number,
        payment_intent_id,
    )

    try:
        await email_service.send_refund_notification(order_response)
    except Exception as exc:
        logger.error(
            "Failed to send refund notification for %s: %s",
            order.order_number,
            exc,
        )


async def _handle_dispute_created(
    db: AsyncSession,
    payment_intent_id: str | None,
) -> None:
    """
    Chargeback filed — mark order as DISPUTED and log CRITICAL so it surfaces
    in monitoring. No customer email — this requires manual admin review.
    """
    try:
        order = await _load_order_by_payment_intent(db, payment_intent_id)
    except NotFoundError:
        logger.critical(
            "CHARGEBACK FILED — no order found for payment_intent=%s "
            "— manual reconciliation required",
            payment_intent_id,
        )
        return

    order.status = OrderStatusEnum.DISPUTED

    logger.critical(
        "CHARGEBACK FILED: order=%s payment_intent=%s "
        "— immediate manual review required",
        order.order_number,
        payment_intent_id,
    )


# ── Other providers ───────────────────────────────────────────────────────────


async def handle_tabby_webhook(payload: bytes, signature: str) -> dict:
    """Stub: Tabby webhook handler. Always acknowledges receipt."""
    logger.info("Tabby webhook received (stub) — not yet processed")
    return {"received": True}


async def handle_tamara_webhook(payload: bytes, signature: str) -> dict:
    """Stub: Tamara webhook handler. Always acknowledges receipt."""
    logger.info("Tamara webhook received (stub) — not yet processed")
    return {"received": True}


async def get_status(db: AsyncSession, order_number: str) -> dict:
    """Return payment status for an order."""
    order = await _load_order(db, order_number)

    paid = stripe_provider.is_confirmed_payment_id(order.payment_id)

    return {
        "order_number": order.order_number,
        "payment_provider": order.payment_provider,
        "payment_method": order.payment_method,
        "payment_id": order.payment_id,
        "paid": paid,
        "order_status": order.status,
    }
