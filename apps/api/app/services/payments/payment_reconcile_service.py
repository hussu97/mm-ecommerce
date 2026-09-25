"""Reconcile card payments whose webhook never arrived, by asking the gateway.

A card order leaves `created` only when its gateway's paid webhook lands. Stripe
retries that webhook for three days and Ziina three times; Paymob documents no
retry policy at all. So an order can be paid at the gateway and sit at `created`
here — the customer charged, the kitchen never told — until the 48-hour expiry
sweep cancels it and restocks a box somebody bought.

This sweep closes that gap for every gateway that can be asked
(`PaymentGatewayProvider.fetch_outcome` — Paymob today). Every
`_SWEEP_EVERY_MINUTES` it picks online orders still `created` or
`payment_failed` with a pending attempt on such a gateway, asks the gateway what
became of each attempt, and applies a *success* through
`payment_service.apply_reconciled_event` — the same dedup and the same handlers
a webhook uses, with the same event id, so a late webhook for the same payment
is a no-op and a payment is never confirmed twice. Only success is acted on:
a decline is already told to the customer by the hosted page, and closing out
abandoned checkouts is the expiry sweep's job.

Shape borrowed from `abandoned_checkout_service`: an advisory lock so one
worker sweeps, candidates read in one short session that closes before any
gateway call, and each order applied in its own short session so one failure
cannot take the rest of the batch with it. Never started unless a gateway that
can be reconciled is actually configured here — in production today that is
none, and the loop does not run.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, heartbeat
from app.core.database import AsyncSessionFactory
from app.models.base import utcnow
from app.models.order import Order, OrderStatusEnum
from app.models.payment_transaction import (
    PaymentTransaction,
    PaymentTransactionStatusEnum,
)
from app.models.pos_order import OrderSourceEnum
from app.services.payments import payment_service
from app.services.payments.payment_gateway_router import PROVIDERS
from app.services.providers.base import PaymentEventType, PaymentGatewayProvider

logger = logging.getLogger(__name__)

__all__ = ["enabled", "reconcilable_gateways", "run_forever", "sweep_once"]

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 16.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_4810

_SWEEP_EVERY_MINUTES = 10

#: Old enough that the webhook has had its chance (they land within seconds),
#: young enough to still matter — and past the 48h expiry sweep, so an order that
#: sweep declined to cancel because its gateway said "paid" is still picked up.
_NOT_BEFORE = timedelta(minutes=15)
_NOT_AFTER = timedelta(hours=72)

#: Plenty for a shop this size; bounds one tick's gateway calls.
_BATCH = 50


def reconcilable_gateways() -> dict[str, PaymentGatewayProvider]:
    """Gateways that can be asked about a payment *and* are configured here."""
    return {
        code: provider
        for code, provider in PROVIDERS.items()
        if type(provider).fetch_outcome is not PaymentGatewayProvider.fetch_outcome
        and provider.is_configured()
    }


def enabled() -> bool:
    return bool(reconcilable_gateways())


async def _candidates(db, gateways: set[str], *, now: datetime) -> list[Order]:
    rows = await db.scalars(
        select(Order)
        .join(PaymentTransaction, PaymentTransaction.order_id == Order.id)
        .where(
            Order.source == OrderSourceEnum.ONLINE.value,
            Order.status.in_((OrderStatusEnum.CREATED, OrderStatusEnum.PAYMENT_FAILED)),
            Order.created_at <= now - _NOT_BEFORE,
            Order.created_at >= now - _NOT_AFTER,
            PaymentTransaction.gateway.in_(gateways),
            PaymentTransaction.status == PaymentTransactionStatusEnum.PENDING.value,
            PaymentTransaction.session_id.is_not(None),
        )
        .options(selectinload(Order.payment_transactions))
        .order_by(Order.created_at)
        .distinct()
        .limit(_BATCH)
    )
    return list(rows)


async def sweep_once(now: datetime | None = None) -> int:
    """One pass. Returns how many payments were applied (0 when another worker
    holds the lock or nothing can be reconciled here)."""
    gateways = reconcilable_gateways()
    if not gateways:
        return 0
    now = now or utcnow()

    async with advisory_lock.held(_ADVISORY_LOCK_KEY, name="payment reconcile") as mine:
        if not mine:
            return 0

        async with AsyncSessionFactory() as db:
            orders = await _candidates(db, set(gateways), now=now)
            attempts = [
                (order.order_number, attempt)
                for order in orders
                for attempt in order.payment_transactions
                if attempt.gateway in gateways
                and attempt.status == PaymentTransactionStatusEnum.PENDING.value
                and attempt.session_id
            ]

        applied = 0
        for order_number, attempt in attempts:
            provider = gateways[attempt.gateway]
            try:
                # The network call, outside any database session.
                event = await provider.fetch_outcome(attempt)
            except Exception:  # noqa: BLE001 — one gateway hiccup, not the sweep
                logger.warning(
                    "Could not ask %s about %s",
                    attempt.gateway,
                    order_number,
                    exc_info=True,
                )
                continue
            if event is None or event.event_type is not PaymentEventType.SUCCEEDED:
                continue

            async with AsyncSessionFactory() as db:
                try:
                    outcome = await payment_service.apply_reconciled_event(
                        db, attempt.gateway, event
                    )
                    # A background job with its own session has no request to
                    # commit for it (CLAUDE.md rule 2): this is the commit.
                    await db.commit()
                except Exception:  # noqa: BLE001 — isolate each order
                    await db.rollback()
                    logger.exception(
                        "Reconciling %s payment for %s failed",
                        attempt.gateway,
                        order_number,
                    )
                    continue
            if outcome.get("applied"):
                applied += 1
                logger.critical(
                    "Reconciled a %s payment for %s whose webhook never arrived",
                    attempt.gateway,
                    order_number,
                )
        return applied


async def run_forever() -> None:
    """The loop. Cancelled on shutdown; never allowed to die on an exception."""
    logger.info(
        "Payment reconcile sweep started (every %dm, gateways: %s)",
        _SWEEP_EVERY_MINUTES,
        ", ".join(sorted(reconcilable_gateways())),
    )
    while True:
        try:
            await asyncio.sleep(_SWEEP_EVERY_MINUTES * 60)
            await heartbeat.beat("payment_reconcile")
            applied = await sweep_once()
            if applied:
                logger.info("Payment reconcile applied %s payment(s)", applied)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not end the loop
            logger.exception("Payment reconcile tick failed")
