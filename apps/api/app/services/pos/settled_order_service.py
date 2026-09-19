"""Close counter checks that were paid but never closed.

A register's pay sequence is two writes: `record_payment` inserts the tender, and
`close_order` transitions the order (`created → confirmed → delivered`), stamps
`closed_at`, fires inventory depletion and releases the table. The two are
deliberately separate — the terminal fires the kitchen ticket between them, and
`send-to-kitchen` 409s on a closed order — but that means a sale can strand: if
the connection drops after the payment lands and before the close does, the money
is taken, the kitchen docket has printed, and the order sits at `created` with a
zero balance and no receipt. The terminal offers no recovery once its pay
pipeline threw, so the check stays open for good (POS-K001-2026-09-19-0063).

This is the safety net. Hourly-ish it finds any `cashier` check that is still
open, priced, and fully paid past a short grace window, and closes it through the
ordinary `close_order` path — so the same transitions, fee stamping and inventory
depletion run as if the till had finished the job, attributed to the cashier who
took the payment. The register-side fix (completing the close in-session) is what
should make this fire on nothing; this guarantees no paid sale is ever stranded
regardless of what the connection did.

Same lifespan reasons as its neighbours: no cron in this stack, an advisory lock
so a second copy across blue/green is harmless, storefront app only.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.exceptions import AppError
from app.models.base import utcnow
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderPayment, OrderSourceEnum
from app.models.user import User
from app.services.pos import pos_order_service

logger = logging.getLogger(__name__)

#: Same flat 64-bit namespace as every other advisory lock. "mmBATCH" + 0C, the
#: next free value after the aggregator/abandoned-cart family.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480C

#: Every two minutes. A stranded sale should be closed promptly — a customer is
#: waiting on the receipt — but the sweep is a cheap indexed read plus a handful
#: of closes, and the register itself should already have finished the job.
_TICK_SECONDS = 120

#: Don't touch a check younger than this: an ordinary close completes in under a
#: second, and the observed stall retried for ~two minutes before giving up. A
#: grace this side of that keeps the sweep from racing a close still in flight.
_GRACE = timedelta(minutes=3)

__all__ = ["run_forever", "sweep_settled_open_orders"]


async def sweep_settled_open_orders(
    db: AsyncSession, *, now: datetime | None = None
) -> list[str]:
    """Close every fully-paid counter check left open past the grace window.

    A candidate is a `cashier` order still at `created`/open `pos_status` with no
    `closed_at`, older than the grace window, whose non-refund payments cover its
    total. Each is re-loaded and re-checked against the real close guard, then
    closed via `close_order` on behalf of the cashier who took the payment — the
    same path the till would have run. Each close is savepointed so one that
    cannot proceed (already closed in the gap, voided) is skipped without losing
    the others; the caller commits. Returns the order numbers closed.
    """
    cutoff = (now or utcnow()) - _GRACE

    net_paid = func.coalesce(
        func.sum(
            case(
                (OrderPayment.is_refund, -OrderPayment.amount),
                else_=OrderPayment.amount,
            )
        ),
        0,
    )
    paid = (
        select(OrderPayment.order_id, net_paid.label("net_paid"))
        .group_by(OrderPayment.order_id)
        .subquery()
    )
    candidates = (
        (
            await db.execute(
                select(Order.id)
                .join(paid, paid.c.order_id == Order.id)
                .where(
                    Order.source == OrderSourceEnum.CASHIER.value,
                    Order.status == OrderStatusEnum.CREATED.value,
                    Order.pos_status.in_(pos_order_service.OPEN_STATUSES),
                    Order.closed_at.is_(None),
                    Order.created_at < cutoff,
                    paid.c.net_paid >= Order.total,
                )
            )
        )
        .scalars()
        .all()
    )

    closed: list[str] = []
    for order_id in candidates:
        order = await pos_order_service.get_order(db, order_id)
        # Re-check on the freshly loaded row: the candidate query ran before the
        # lock and a concurrent close/void may have moved the order since.
        if (
            order.source != OrderSourceEnum.CASHIER.value
            or order.status != OrderStatusEnum.CREATED.value
            or order.pos_status not in pos_order_service.OPEN_STATUSES
            or order.balance_due > 0
        ):
            continue
        # Close on behalf of whoever took the money, so `closer_id` and the status
        # event read true rather than naming a system actor. A settled check
        # always has a non-refund payment, but guard anyway.
        payer_id = next(
            (p.user_id for p in reversed(order.payments) if not p.is_refund), None
        )
        if payer_id is None:
            continue
        cashier = await db.get(User, payer_id)
        if cashier is None:
            continue
        try:
            async with db.begin_nested():
                await pos_order_service.close_order(db, order=order, user=cashier)
            closed.append(order.order_number)
        except AppError as exc:
            # A close that cannot proceed (already closed in the gap, a till that
            # closed under it) is not the sweep's error to raise on.
            logger.info(
                "settled-order sweep: left %s open — %s", order.order_number, exc
            )
    return closed


async def run_forever() -> None:
    """Close any paid-but-open counter check, on a leader-elected loop.

    Leader-elected on an advisory lock and beating its heartbeat, the same shape
    as the other lifespan loops — no cron in this stack, one worker inside a sweep
    at a time.
    """
    logger.info("Settled-order sweeper started (every %ss)", _TICK_SECONDS)
    while True:
        try:
            # Sleeps first: boot is busy and nothing here is urgent.
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("settled_order_sweeper")
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="settled order sweeper"
            ) as db:
                if db is None:
                    continue
                closed = await sweep_settled_open_orders(db)
                await db.commit()
                if closed:
                    logger.info(
                        "Settled-order sweeper closed %s stranded check(s): %s",
                        len(closed),
                        closed,
                    )
        except asyncio.CancelledError:
            logger.info("Settled-order sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Settled-order sweeper tick failed")
