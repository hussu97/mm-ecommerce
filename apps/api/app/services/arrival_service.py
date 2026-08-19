"""
When the shop is told to make an order, and what that costs.

`confirmed` means the money arrived. It used to mean the register heard about
it too, and for most zones that is still the same minute — but for a zone whose
orders ride a shared run it is hours out, and collapsing the two made the
question "has the shop been told about this order" unanswerable from the order.
`arrived_at_pos` is that question, given a column of its own.

**The rule, in one line: the shop is told when the van is booked.** A run leaves
at the close of its window, so a batched order reaches the register then and not
before, and the ticket that prints carries a courier reference that already
exists. Everything else — a third-party zone, a pickup, a courier with no
schedule — is dispatched the moment it is confirmed, so for those two the
confirmation and the arrival are one act.

**A shut shop is not told anything.** An order placed at 03:00 used to land on a
dark counter and ring at nobody until morning; its arrival is now held to the
branch's next opening. Which means the hours are enforced on the arrival rather
than on the acceptance, and the register's own out-of-hours refusal
(`pos_order_service.may_auto_accept`) becomes a second line rather than the
only one. That refusal is deliberately left alone: it keys off *placement* time,
so a six-hour-old order still asks for a person's judgement when it lands, which
is the behaviour the shop asked for and the reason the alarm exists.

**Arrival is never conditional on the dispatch succeeding.** A courier that
refuses — an empty wallet, an address it will not serve — leaves an order that
is paid for and still has to be made. The failure goes on the delivery row for
the retry ladder and the shop is told regardless. Getting this backwards would
mean a courier outage silently stopped the kitchen.

The two callers are `order_lifecycle` (at confirmation, to stamp `arrives_at`)
and `batch_scheduler` (each tick, to land everything now due).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import trading_hours
from app.models.branch import Branch
from app.models.order import Order, OrderStatusEnum
from app.models.order_status_event import StatusSourceEnum, acting_as
from app.models.pos_order import OrderSourceEnum

logger = logging.getLogger(__name__)

__all__ = ["due", "land", "schedule", "sweep"]

#: How old a confirmed order can be and still be something the kitchen is
#: waiting to make. Past this it is abandoned — and announcing it as new work
#: is worse than leaving it, because somebody has to work out what it is.
_STALE_AFTER = timedelta(days=2)

#: How many orders one tick will land. Generous — a window closing on a busy
#: evening empties into the kitchen all at once, and holding some of it back to
#: the next tick would only stagger the same burst by a minute.
_SWEEP_LIMIT = 200


async def schedule(db: AsyncSession, order: Order) -> datetime | None:
    """
    Decide when this order reaches the register, and stamp it.

    Called once, on confirmation, before anything has been told about the
    order. Returns the moment stamped, or None for an order that never reaches
    a register at all.

    **The run is joined here rather than at arrival**, which is the one piece of
    ordering the whole design turns on: a batch is made of the orders assigned
    to it, and its window closing is what triggers their arrival. An order that
    waited for arrival before joining would be waiting for a run it was not on.
    """
    if order.source != OrderSourceEnum.ONLINE.value:
        # A counter sale is rung up by a person who is holding it. There is no
        # arrival to schedule and nothing to tell anybody.
        return None

    from app.services import batching_service

    # Takes its place on a run, if its zone has one. Books nothing: the whole
    # point is that the booking happens when the run leaves.
    reserved = await batching_service.reserve(db, order)

    if reserved is not None and reserved.is_open:
        # A batched order. It arrives when its run does.
        order.arrives_at = reserved.dispatch_at
    else:
        order.arrives_at = await _next_working_moment(db, order)

    logger.info(
        "Order %s reaches the register at %s",
        order.order_number,
        order.arrives_at.isoformat() if order.arrives_at else "never",
    )

    # Due already, so land it in the same breath rather than leaving it for the
    # sweep. Most orders are this case — every pickup, every third-party zone,
    # every courier without a schedule — and for them confirmation and arrival
    # are one instant, exactly as they were before the status existed. Waiting
    # for the next tick would put up to a minute between a customer paying cash
    # at the counter and the counter hearing about it.
    #
    # Re-entrant on purpose: this runs inside `transition`'s consequences for
    # `confirmed`, and moves the order on to `arrived_at_pos` from there. The
    # sweep remains the backstop for anything this misses — a rollback between
    # here and the flush, a branch that was shut when the money landed.
    if order.arrives_at is not None and order.arrives_at <= datetime.now(timezone.utc):
        await land(db, order, because="confirmed, with no run to wait for")

    return order.arrives_at


async def _next_working_moment(db: AsyncSession, order: Order) -> datetime:
    """Now, or the branch's next opening if the shop is shut."""
    now = datetime.now(timezone.utc)
    if order.branch_id is None:  # pragma: no cover — column is NOT NULL
        return now

    branch = await db.get(Branch, order.branch_id)
    if branch is None:  # pragma: no cover — FK is RESTRICT
        return now

    from app.services import branch_holiday_service

    closed = await branch_holiday_service.closed_dates_for(db, order.branch_id)
    if trading_hours.is_open(now, branch.opening_from, branch.opening_to, closed):
        return now
    return trading_hours.next_opening(now, branch.opening_from, closed)


async def due(db: AsyncSession, *, limit: int = _SWEEP_LIMIT) -> list[Order]:
    """
    Confirmed orders whose moment has come.

    Deliberately not filtered on `arrives_at` being non-null: an order confirmed
    before this existed, or one whose stamp was lost to a rollback between the
    confirmation and its flush, would otherwise sit at `confirmed` forever with
    nobody making it. A null stamp reads as *overdue*, because an order nobody
    can date is one nobody is baking.

    **Bounded by age, which that generosity makes necessary.** An order
    confirmed months ago and never published is abandoned, not waiting — a
    checkout that half-failed, a test order, a gateway event nobody finished
    handling. Swept up it is announced as new work: three Stripe test orders
    from March reached every terminal in the branch and rang the
    unaccepted-order alarm once a minute until somebody stopped it. Nothing a
    kitchen can still act on is more than a couple of days old, and anything
    older needs a person rather than a sweep.
    """
    now = datetime.now(timezone.utc)
    return list(
        (
            await db.execute(
                select(Order)
                .where(
                    Order.status == OrderStatusEnum.CONFIRMED,
                    Order.source == OrderSourceEnum.ONLINE.value,
                    (Order.arrives_at.is_(None)) | (Order.arrives_at <= now),
                    Order.created_at > now - _STALE_AFTER,
                )
                # `items` for the cancellation restock a consequence may
                # walk, `delivery` so the history can say *why* each order
                # landed rather than only that it did.
                .options(selectinload(Order.items), selectinload(Order.delivery))
                .order_by(Order.arrives_at.nulls_first(), Order.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def land(db: AsyncSession, order: Order, *, because: str) -> bool:
    """
    Put one order on the register, and get a driver moving for it.

    Returns whether it moved. False means something else got there first, which
    is an ordinary outcome: an admin marking an order packed, or a courier
    reporting a pickup, both leave `confirmed` without passing through here.

    `because` is the reason this order landed *now*, and it goes in the status
    history. Worth a parameter rather than one fixed string, because the reasons
    are genuinely different questions later — a run leaving, a shop opening, and
    a zone with nothing to wait for all arrive here and only the note tells them
    apart.

    **Attributed explicitly, and that matters more than it looks.** The inline
    path runs inside `_consequences` for `confirmed`, which on the ordinary
    route is itself inside `payment_service`'s
    `acting_as(PAYMENT, actor_label="stripe", note="payment_intent.succeeded")`.
    Inheriting that would record Stripe as having put the order on the register,
    with a payment event id as the reason — neither of which happened. The
    arrival is our own scheduling decision, so it says so, and the context
    manager nests properly: the payment's attribution is restored on the way
    out and still covers the confirmation itself.

    The dispatch is best-effort and comes second, in that order on purpose. A
    courier that is unreachable must not keep the kitchen from being told: the
    box has to be made either way, and `assign_or_dispatch` writes its own
    failure to the delivery row where the retry ladder and the admin's
    needs-a-human list both read it.
    """
    from app.services import batching_service, order_lifecycle

    with acting_as(StatusSourceEnum.SYSTEM.value, note=because):
        moved = await order_lifecycle.transition(
            db, order, OrderStatusEnum.ARRIVED_AT_POS, on_invalid="skip"
        )
    if not moved:
        return False

    try:
        await batching_service.assign_or_dispatch(db, order)
    except Exception:  # noqa: BLE001 — a courier must not stop the kitchen
        logger.exception(
            "Booking a courier for %s blew up on arrival; it needs a person",
            order.order_number,
        )
    return True


async def sweep(db: AsyncSession, *, limit: int = _SWEEP_LIMIT) -> list[str]:
    """
    Land everything due, one at a time. Returns the order numbers that moved.

    Sequential rather than concurrent, for the same reason the register accepts
    its queue one at a time: each arrival prints, and a window emptying into a
    kitchen should reach the bench in the order the orders were placed rather
    than in the order the network settled.
    """
    landed: list[str] = []
    for order in await due(db, limit=limit):
        try:
            if await land(db, order, because=_why(order)):
                landed.append(order.order_number)
        except Exception:  # noqa: BLE001 — one bad order must not hold the rest
            logger.exception("Could not land %s on the register", order.order_number)
            await db.rollback()
    return landed


def _why(order: Order) -> str:
    """
    The reason this order is landing now, for its status history.

    Three things bring an order here and they are different questions later: a
    run leaving, a shop opening, and an order whose stamp went missing. A
    history that recorded only "arrived" would answer none of them — and the
    third is the one worth being able to find, because it means something went
    wrong at confirmation and the sweep is covering for it.
    """
    if order.delivery is not None and order.delivery.batch_id is not None:
        return "its run left"
    if order.arrives_at is None:
        return "no arrival was scheduled; swept up"
    return "its arrival fell due"
