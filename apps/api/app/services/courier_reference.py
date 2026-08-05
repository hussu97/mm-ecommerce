"""
The short number a driver reads back to us.

Our own order numbers are `MM-20260805-007` — fifteen characters, three of them
punctuation, and the interesting part at the end. That is a fine identifier for a
database and a bad one for somebody holding a phone in one hand and a cake box in
the other. noon Send asked us for something shorter so their riders can quote it,
and the same argument applies to Lalamove's driver notes.

Seven digits, drawn at random rather than counted up. A sequence would be one
line shorter and would also tell every driver, every day, exactly how many orders
the shop has ever taken; a random number in a nine-million-wide space says
nothing and collides essentially never.

**Globally unique, not per-courier.** noon Send only needs the number to be
unique within their own records, and Lalamove likewise — but one index over the
whole table satisfies both at once, survives an order that falls back from noon
Send to Lalamove mid-dispatch (the reference stays; only the provider changes),
and means a support call that opens with seven digits identifies exactly one
order without anybody having to ask which courier it went with.

Assigned at dispatch rather than at checkout, so a third-party zone — which never
meets a courier — never spends one.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order_delivery import OrderDelivery

logger = logging.getLogger(__name__)

__all__ = ["assign", "generate"]

#: 1000000–9999999. Seven digits every time — no leading zero to be lost by a
#: spreadsheet, a phone keypad or a courier's own integer column.
_LOW = 1_000_000
_SPAN = 9_000_000

#: Enough that exhausting them means something is wrong rather than unlucky. At
#: ten thousand orders the chance of one collision is about 0.1%; six in a row is
#: not a number worth writing down.
_ATTEMPTS = 6


def generate() -> str:
    """One candidate. `secrets` rather than `random`: it costs nothing here."""
    return str(_LOW + secrets.randbelow(_SPAN))


async def assign(db: AsyncSession, delivery: OrderDelivery) -> str | None:
    """
    Give this delivery a short reference, or leave it without one.

    Idempotent: a re-dispatch keeps the number it already had. The reference
    identifies the order, not the booking, so a driver quoting it after a second
    attempt still reaches the right cake.

    Returns `None` when no free number was found, which the callers treat as
    "use the order number this time". A dispatch must never fail because of the
    label on it — the customer has paid and the box is packed.
    """
    if delivery.courier_reference:
        return delivery.courier_reference

    for _ in range(_ATTEMPTS):
        candidate = generate()
        if await _taken(db, candidate):
            continue
        # The read above is a courtesy, not the guarantee — two dispatches can
        # pass it with the same number. The unique index is the guarantee, and a
        # savepoint is what lets us find out without losing the transaction that
        # is in the middle of booking a courier.
        try:
            async with db.begin_nested():
                delivery.courier_reference = candidate
                await db.flush()
            return candidate
        except IntegrityError:
            delivery.courier_reference = None
            continue

    logger.warning(
        "No free courier reference after %s attempts for delivery %s — "
        "falling back to the order number",
        _ATTEMPTS,
        delivery.id,
    )
    return None


async def _taken(db: AsyncSession, candidate: str) -> bool:
    return (
        (
            await db.execute(
                select(OrderDelivery.id).where(
                    OrderDelivery.courier_reference == candidate
                )
            )
        )
        .scalars()
        .first()
    ) is not None
