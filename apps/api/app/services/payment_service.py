from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.order import Order, OrderStatusEnum
from app.services.providers import stripe_provider, tabby_provider, tamara_provider

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "stripe": stripe_provider,
    "tabby": tabby_provider,
    "tamara": tamara_provider,
}


def _get_provider(name: str):
    p = _PROVIDERS.get(name.lower())
    if not p:
        raise BadRequestError(
            f"Unknown payment provider '{name}'. Supported: stripe, tabby, tamara"
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


async def create_session(db: AsyncSession, order_number: str, provider: str) -> dict:
    """
    Create a payment checkout session for the given order.
    Stores session_id in order.payment_id. Returns {provider, session_id, checkout_url}.
    """
    order = await _load_order(db, order_number)

    if order.status == OrderStatusEnum.CANCELLED:
        raise BadRequestError("Cannot create payment session for a cancelled order")

    # Idempotency: if already has a confirmed payment, reject
    if stripe_provider.is_confirmed_payment_id(order.payment_id):
        raise BadRequestError("Order has already been paid")

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
    On checkout.session.completed: update order.payment_id to the payment intent ID.
    Always returns a dict for the HTTP response.
    """
    result = stripe_provider.handle_webhook(payload, signature)
    event_type = result["event_type"]
    order_number = result.get("order_number")
    payment_intent_id = result.get("payment_intent_id")

    if event_type == "checkout.session.completed" and order_number:
        try:
            order = await _load_order(db, order_number)
            if payment_intent_id:
                order.payment_id = payment_intent_id
            await db.flush()
            logger.info(
                "Payment confirmed: order=%s payment_intent=%s",
                order_number,
                payment_intent_id,
            )
        except NotFoundError:
            logger.error("Webhook: order not found for order_number=%s", order_number)

    elif event_type in ("checkout.session.expired", "payment_intent.payment_failed"):
        logger.warning(
            "Payment not completed: event=%s order=%s", event_type, order_number
        )

    return {"received": True, "event_type": event_type}


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
    # BNPL providers (Tabby/Tamara) require webhook confirmation — payment_id alone is not proof

    return {
        "order_number": order.order_number,
        "payment_provider": order.payment_provider,
        "payment_method": order.payment_method,
        "payment_id": order.payment_id,
        "paid": paid,
        "order_status": order.status,
    }
