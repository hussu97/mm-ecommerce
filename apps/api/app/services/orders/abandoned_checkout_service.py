"""Abandoned-cart recovery: remind a customer to finish a checkout they started.

An online order is written as `created` before the customer is sent to the card
gateway, and it only leaves `created` when the gateway's paid webhook arrives. So
a customer who opened the payment page and never paid leaves an order sitting at
`created` with no money taken and no further signal — the cart is saved, but
nobody knows to come back to it.

This sweep finds those orders once they are `ABANDONED_CART_AFTER_MINUTES` old (and
not so old the gateway session has expired), and emails the customer a reminder
with the cart contents and a link straight to the gateway's own still-live hosted
payment page (`provider.resume_url`). Sent once per order — the guard is the email
journal (`email_service.already_sent`), so a paid order (which has left `created`)
and an already-reminded one are both skipped without a new column.

Shape borrowed from `log_retention` / `delivery_scheduler`: an advisory lock so
only one worker sweeps, a loop that survives a bad tick, and a heartbeat. The lock
is held WITHOUT pinning a work session across the per-order gateway and email
calls (those are third-party awaits) — candidates are read in one short session
that closes first, then the network work runs unlocked-from-the-DB but still under
the advisory lock.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, heartbeat
from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.base import utcnow
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum
from app.services import email_service
from app.services.orders import order_service
from app.services.payments.payment_gateway_router import PROVIDERS

logger = logging.getLogger(__name__)

__all__ = ["run_forever", "sweep_once"]

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 11.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480B


async def _candidates(db, *, now: datetime) -> list[Order]:
    """Online orders still `created`, old enough to nudge but not so old the
    gateway session has expired, with an email to send to. Items eager-loaded so
    `to_response` renders the cart without a lazy load."""
    oldest = now - timedelta(hours=max(settings.ABANDONED_CART_MAX_AGE_HOURS, 0))
    newest = now - timedelta(minutes=max(settings.ABANDONED_CART_AFTER_MINUTES, 0))
    rows = await db.scalars(
        select(Order)
        .where(
            Order.source == OrderSourceEnum.ONLINE.value,
            Order.status == OrderStatusEnum.CREATED,
            Order.email.is_not(None),
            Order.email != "",
            Order.created_at <= newest,
            Order.created_at >= oldest,
        )
        .order_by(Order.created_at)
        .options(selectinload(Order.items))
    )
    return list(rows)


async def sweep_once(now: datetime | None = None) -> int:
    """One pass: find abandoned checkouts and mail each one a resume link. Returns
    the number of reminders sent. `{}`-style no-op (0) when disabled or when
    another worker holds the lock."""
    if not settings.ABANDONED_CART_EMAIL_ENABLED:
        return 0
    now = now or utcnow()

    async with advisory_lock.held(
        _ADVISORY_LOCK_KEY, name="abandoned cart"
    ) as mine:
        if not mine:
            return 0

        # Read every candidate (and build its customer-facing response) inside one
        # short session that closes BEFORE any gateway/email call — DB-only work,
        # so the connection is never held idle across a third-party await.
        prepared: list[tuple[Order, object]] = []
        async with AsyncSessionFactory() as db:
            for order in await _candidates(db, now=now):
                response = await order_service.to_response(db, order)
                prepared.append((order, response))

        sent = 0
        for order, response in prepared:
            try:
                # Once only: the journal already holds a sent `abandoned_cart` for
                # this order if a prior tick mailed it. Fails open (mails again)
                # only if the journal is unreachable, which is the safer error.
                if await email_service.already_sent(
                    order.order_number, "abandoned_cart.html"
                ):
                    continue
                provider = PROVIDERS.get(order.payment_provider or "")
                if provider is None:
                    continue
                # The gateway's own still-live hosted page, or None if the session
                # has expired / been paid / cancelled — in which case there is no
                # live link to send, so skip rather than mail a dead one.
                resume_url = await provider.resume_url(order)
                if not resume_url:
                    continue
                await email_service.send_abandoned_cart(response, resume_url=resume_url)
                sent += 1
            except Exception:  # noqa: BLE001 — one order must not stop the sweep
                logger.exception(
                    "abandoned-cart reminder failed for %s", order.order_number
                )
        return sent


async def run_forever() -> None:
    """The loop. Cancelled on shutdown; never allowed to die on an exception."""
    interval = settings.ABANDONED_CART_SWEEP_MINUTES
    if interval <= 0:
        logger.info("Abandoned-cart sweep disabled (interval <= 0)")
        return
    logger.info(
        "Abandoned-cart sweep started (every %dm; nudge at %dm, max age %dh)",
        interval,
        settings.ABANDONED_CART_AFTER_MINUTES,
        settings.ABANDONED_CART_MAX_AGE_HOURS,
    )
    while True:
        try:
            # Sleeps first, like its scheduler neighbours: boot is the busiest
            # moment and nothing here is urgent to the second.
            await asyncio.sleep(interval * 60)
            await heartbeat.beat("abandoned_cart")
            sent = await sweep_once()
            if sent:
                logger.info("Abandoned-cart sweep sent %d reminder(s)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Abandoned-cart sweep tick failed")
