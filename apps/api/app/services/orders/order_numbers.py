"""Filing a new order under a day-series number that another writer may race for.

Every series here (`AGG-`, `MM-`, `POS-`) numbers by reading today's highest and
adding one, and that read cannot see a row another transaction has inserted but
not yet committed. Two writers of one series can therefore draw the same number,
and the second insert dies on `ix_orders_order_number`.

The aggregator series has two writers — the GrubOps ingest and marketplace
promotion — and on 2026-10-12 they collided: both drew AGG-20261012-046, the
GrubOps write lost, and its retry forty seconds later filed the order as -047
with a different pickup code on the ticket. A lock does not fit: promotion
commits once per pass, so a transaction lock taken at numbering would hold every
GrubOps order back for the whole pass. Instead the insert runs in a SAVEPOINT
and, on that one constraint, draws the number again — by then the winner has
committed, so the fresh read sees it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order

#: The unique index the race trips. Anything else is a real error and re-raised.
_ORDER_NUMBER_INDEX = "ix_orders_order_number"
_ATTEMPTS = 5


async def add_with_fresh_number(
    db: AsyncSession,
    order: Order,
    next_number: Callable[[AsyncSession], Awaitable[str]],
) -> None:
    """`db.add` + flush `order`, drawing a new `order_number` if another writer
    took it first."""
    for attempt in range(_ATTEMPTS):
        try:
            async with db.begin_nested():
                db.add(order)
                await db.flush()
            return
        except IntegrityError as exc:
            if _ORDER_NUMBER_INDEX not in str(exc.orig) or attempt == _ATTEMPTS - 1:
                raise
            order.order_number = await next_number(db)
