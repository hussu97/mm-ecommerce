"""
The one place an order's status is allowed to change.

`Order.status` used to be assigned from thirteen places behind five independent
sets of rules: `VALID_TRANSITIONS` bound the admin endpoint, the payment
webhooks kept a private `_CONFIRMABLE_FROM`, each courier carried its own
hand-rolled guard, and the register wrote raw strings with no check at all. The
five had already drifted into contradiction — `undelivered` was made terminal
here while one courier still allowed a way back out — and the consequences of a
transition (the refund, the restock, the courier cancellation) fired only on
the path that happened to be the admin's.

The cost of that was concrete: an order the *courier* marked undelivered never
refunded, because the automatic refund lived only in `update_status` — while
the email that path sent promised the customer their money was on its way.

So: one function. `transition()` validates against `VALID_TRANSITIONS`,
assigns the column, and carries the consequences, keyed off the transition
rather than the endpoint. Callers with a legitimately special case name it in
`extra_from` instead of maintaining a private guard set, and callers that must
never raise on stale news — webhooks — say `on_invalid="skip"`.

Attribution is not this module's job. `acting_as` at the entry points and the
attribute listener in `models/order_status_event.py` already record every
write, whoever makes it; that machinery is untouched and remains impossible to
bypass. This module adds the twin listener that *warns* about writes made
outside `transition()`, so a new direct assignment shows up in the logs before
it shows up as a drifted state machine.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Collection, Literal

from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import event

from app.core.exceptions import BadRequestError
from app.models.order import Order, OrderStatusEnum
from app.models.pos_order import OrderSourceEnum, PosOrderStatusEnum
from app.models.product import Product

__all__ = [
    "ADMIN_RECOVERABLE",
    "VALID_TRANSITIONS",
    "can_transition",
    "transition",
]

logger = logging.getLogger(__name__)


# Valid status transitions
VALID_TRANSITIONS: dict[OrderStatusEnum, set[OrderStatusEnum]] = {
    OrderStatusEnum.CREATED: {
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.PAYMENT_FAILED,
    },
    OrderStatusEnum.PAYMENT_FAILED: {
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.CONFIRMED,
        # Retrying the payment. `create_session` resets a failed order so the
        # gateway sees a fresh attempt; it was always allowed to, it just used
        # to do it outside the map.
        OrderStatusEnum.CREATED,
    },
    OrderStatusEnum.CONFIRMED: {
        # The ordinary next step, and for most zones it happens within the
        # minute. A batched order waits here for its run to close.
        OrderStatusEnum.ARRIVED_AT_POS,
        # Kept, though the sweep normally gets there first. An admin correcting
        # an order, or a register running ahead of a sweep that has not ticked,
        # is describing a box that physically exists — refusing that because our
        # own bookkeeping has not caught up would be the tail wagging the dog.
        OrderStatusEnum.PACKED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
        # A rider can collect before anything stamped the order packed — a
        # branch with no register has no acceptance event, and noon Send loses
        # pushes outright. The courier's word that the box is in a van proves
        # the packing happened; refusing the fact because we missed the middle
        # would strand the order at `confirmed` while it drives across town.
        OrderStatusEnum.OUT_FOR_DELIVERY,
        # Same reasoning, one step further: a lost pickup push followed by a
        # delivered push, or an admin recording a collected pickup order,
        # arrives here without ever seeing `packed`.
        OrderStatusEnum.DELIVERED,
    },
    OrderStatusEnum.ARRIVED_AT_POS: {
        OrderStatusEnum.PACKED,
        # **Straight past `packed`, deliberately.** On a zone we dispatch
        # ourselves the courier's PICKED_UP is usually the first news that the
        # box is finished, and the obvious reading — write a `packed` on the way
        # through so the chain looks complete — invents a moment that never
        # happened. `packed_at` is shown to the customer and read by the
        # kitchen reports; a timestamp meaning "the instant we heard about the
        # driver" would be a lie in both places. The chain is allowed to have a
        # hole in it, and the hole is the honest record.
        OrderStatusEnum.OUT_FOR_DELIVERY,
        # A collected pickup, or a delivered push that outran its pickup one.
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    OrderStatusEnum.PACKED: {
        OrderStatusEnum.OUT_FOR_DELIVERY,
        # A third-party zone has no courier reporting back, so the shop marks
        # the order delivered itself and never passes through the middle state.
        OrderStatusEnum.DELIVERED,
        # A driver can also fail to hand it over on a run we never saw start.
        OrderStatusEnum.UNDELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # A courier that rejects, expires or cancels a booking does not cancel the
    # order — it is paid for and boxed. The delivery row records the failure and
    # an admin re-dispatches or refunds, which is why cancelling is still not
    # reachable from here.
    OrderStatusEnum.OUT_FOR_DELIVERY: {
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.UNDELIVERED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # A failed handover, and a place an order can leave again.
    #
    # This was briefly an ending — refund the money, close the order — because
    # treating it as recoverable had let a *sweep* send a second driver for
    # something a person had written off. That was the real fault, and closing
    # the status was too broad a fix: it also meant the shop could not drive a
    # box over itself, could not hand a stuck order to a different courier, and
    # could not act on the commonest case of all, which is a driver reporting no
    # answer at the door and the customer ringing back ten minutes later.
    #
    # So the status reopens and the *refund* is what moves. Nothing is paid back
    # here any more; `_REFUNDABLE_ENDINGS` is `cancelled` alone, and cancelling
    # is how somebody says this order is over. Until they do, the cake exists,
    # it is paid for, and it is still ours to deliver.
    #
    # `packed` rather than `out_for_delivery` is the way back in. That is where
    # the box actually is — on a shelf, made — and it is where dispatch hangs
    # off, so a second attempt runs through `assign_or_dispatch` like any other
    # packed order instead of down a path of its own.
    OrderStatusEnum.UNDELIVERED: {
        OrderStatusEnum.PACKED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    OrderStatusEnum.DELIVERED: {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # Cancelled is an ending, and stays one. The two money outcomes are new:
    # until refunds existed there was nothing to record and `set()` was honest,
    # but a cancelled order is the single most likely thing to be refunded and
    # the status had no way to say so. Neither of these puts it back in the
    # kitchen.
    OrderStatusEnum.CANCELLED: {
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    },
    # Terminal. Reached by a refund we issue or a gateway webhook; nothing
    # leaves.
    OrderStatusEnum.REFUNDED: set(),
    OrderStatusEnum.DISPUTED: set(),
}


#: Register states a cancellation still has to close. `closed`, `void`,
#: `joined` and the rest are already finished, and a cancellation arriving after
#: them must not reopen or relabel what the till already settled.
_OPEN_ON_THE_REGISTER = frozenset(
    {
        PosOrderStatusEnum.DRAFT.value,
        PosOrderStatusEnum.PENDING.value,
        PosOrderStatusEnum.ACTIVE.value,
    }
)


#: The one way an order stops being deliverable with the customer's money still
#: in our account. `refunded` and `disputed` are not here: by the time an order
#: reaches either, the money has already moved.
#:
#: `undelivered` used to be here too, and that is what made a failed handover a
#: write-off — the money went back the moment a driver reported no answer at the
#: door, so there was nothing left to deliver even though the cake was on a
#: shelf. Paying back is a decision somebody makes, not a consequence of a
#: courier's status push, and `cancelled` is where somebody makes it.
_REFUNDABLE_ENDINGS = {
    OrderStatusEnum.CANCELLED,
}


#: Endings a person may take an order back out of, and only a person.
#:
#: Both of these are written down when something has definitively failed, and
#: both are sometimes written down wrongly: a driver reports a failed handover
#: and then finds the customer; a cancellation is keyed against the wrong order
#: number; the shop drives a box over itself after a courier gave up. What
#: follows is not another van — it is a correction of the record.
#:
#: Deliberately **not** in `VALID_TRANSITIONS`. That map is read by the courier
#: webhooks, the register and the checkout as well, and widening it would let a
#: late `DELIVERED` push from a courier silently resurrect an order the shop had
#: already written off and refunded. This reaches the resolver through
#: `extra_from`, which is the module's existing way of saying "this one caller
#: may leave a state the map closes" — and the only caller that passes it is
#: `order_service.update_status`, the admin console's doorway.
ADMIN_RECOVERABLE: dict[OrderStatusEnum, frozenset[OrderStatusEnum]] = {
    OrderStatusEnum.DELIVERED: frozenset(
        {OrderStatusEnum.UNDELIVERED, OrderStatusEnum.CANCELLED}
    ),
}


def can_transition(current: OrderStatusEnum, new: OrderStatusEnum) -> bool:
    """Whether the map allows moving from `current` to `new`."""
    return new in VALID_TRANSITIONS.get(current, set())


#: Set while `transition()` is the one assigning the column, so the listener
#: below can tell an authorised write from a stray one. A ContextVar rather
#: than a flag on the order: two concurrent requests must not vouch for each
#: other's writes.
_GATED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "order_status_write_gated", default=False
)


@event.listens_for(Order.status, "set", active_history=True)
def _warn_on_ungated_write(target: Order, value, oldvalue, _initiator) -> None:
    """
    A status write that did not come through `transition()`.

    A warning rather than an exception, deliberately, for now: the writers this
    refactor knows about all go through the gate, and the ones it does not know
    about are exactly the ones a raise would turn from a logged inconsistency
    into a broken request. Escalate to a raise once the log has been quiet for
    a while. Creation writes (no previous value) stay free — building an order
    in a given state, in code or in a test, is not a transition.
    """
    if _GATED.get():
        return
    old = oldvalue.value if isinstance(oldvalue, OrderStatusEnum) else oldvalue
    new = value.value if isinstance(value, OrderStatusEnum) else value
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return
    logger.warning(
        "Order.status written outside order_lifecycle.transition(): "
        "order=%s %s -> %s. The write stands, but its validation and "
        "consequences were skipped — route it through transition().",
        getattr(target, "order_number", target.id),
        old,
        new,
    )


async def transition(
    db: AsyncSession,
    order: Order,
    new_status: OrderStatusEnum,
    *,
    extra_from: Collection[OrderStatusEnum] = (),
    on_invalid: Literal["raise", "skip"] = "raise",
) -> bool:
    """
    Move an order to `new_status`, with everything that move implies.

    Returns whether anything moved. False means the order was already there or
    the map refused and `on_invalid="skip"` — the caller distinguishing the two
    has the order's status in hand.

    `extra_from` names source statuses this caller may leave beyond what the
    map allows, for the cases where an outside fact outranks our bookkeeping —
    a gateway reporting money returned on an order that never confirmed. It
    widens where a move may start, never where it may end.

    `on_invalid="skip"` is for webhooks and the register: a courier pushing
    stale news about a settled order is an event to decline quietly, not a
    request to fail. Interactive callers keep the default and get the same
    `BadRequestError` the admin endpoint has always raised.

    For a cancellation the caller must have `order.items` loaded — the restock
    walks them, and under asyncpg an unloaded collection is a loud
    `MissingGreenlet` rather than a lazy query. Every current caller loads them.

    No flush and no commit: services flush, the request commits. The refund is
    the one deliberate exception to purity here — it talks to a bank — and it
    swallows its own failures precisely so this transition cannot be held
    hostage by a gateway (see `payment_service.refund_order`).
    """
    if order.status == new_status:
        return False

    if not can_transition(order.status, new_status) and order.status not in set(
        extra_from
    ):
        if on_invalid == "skip":
            logger.info(
                "Refused transition %s -> %s for order %s",
                getattr(order.status, "value", order.status),
                new_status.value,
                order.order_number,
            )
            return False
        allowed = VALID_TRANSITIONS.get(order.status, set())
        raise BadRequestError(
            f"Cannot transition order from '{order.status}' to '{new_status}'. "
            f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}"
        )

    previous = order.status
    token = _GATED.set(True)
    try:
        order.status = new_status
    finally:
        _GATED.reset(token)

    await _consequences(db, order, previous, new_status)
    return True


async def _move_stock(db: AsyncSession, order: Order, direction: int) -> None:
    """
    Put this order's stock back on the shelf (`+1`) or take it off again (`-1`).

    Only a website order has any to move. Checkout decrements `stock_quantity`
    when the order is written; a counter sale depletes recipe ingredients at
    close instead, so touching it here would invent stock it never took — which
    is what cancelling one from the console used to do.

    One function for both directions rather than two nearly-identical loops,
    because they are one fact read forwards and backwards: whatever a
    cancellation gives back, un-cancelling has to take again. Two copies would
    be free to drift, and the drift would show up as stock that is quietly
    wrong rather than as an error.
    """
    if order.source != OrderSourceEnum.ONLINE.value:
        return
    for item in order.items:
        if not item.product_id:
            continue
        await db.execute(
            sql_update(Product)
            .where(Product.id == item.product_id, Product.is_stock_product.is_(True))
            .values(stock_quantity=Product.stock_quantity + direction * item.quantity)
            .execution_options(synchronize_session=False)
        )


async def _consequences(
    db: AsyncSession,
    order: Order,
    previous: OrderStatusEnum,
    new_status: OrderStatusEnum,
) -> None:
    """
    What arriving at a status makes happen, whoever brought the order there.

    Keyed off the destination, with one exception: undoing a cancellation has
    to know it was a cancellation being undone, because what it owes is the
    reverse of what that cancellation did.

    These used to live in `order_service.update_status`, which meant they fired
    only when an admin moved the order: a courier webhook marking an order
    undelivered skipped the refund while its email promised one. Keyed off the
    transition, the machinery is identical for the console, a webhook, the
    checkout and the till.

    The service imports are deferred: this module sits underneath
    `order_service` and the couriers, and importing them at the top would close
    the cycle they already thread carefully around.
    """
    # Confirmation no longer reaches the register. What it does is decide
    # *when* the register will hear: the order takes its place on a run if its
    # zone has one, and is stamped with the moment it is due to arrive.
    #
    # Confirming by hand is a confirmation like any other — a bank transfer
    # reconciled in the morning, or a declined card the customer paid another
    # way — so this is keyed off the transition and not off the endpoint.
    if new_status == OrderStatusEnum.CONFIRMED:
        from app.services import arrival_service

        await arrival_service.schedule(db, order)

    # The register hears about it here instead, which is the point of the
    # status. For a batched zone this is hours after the money landed and the
    # same instant the van is booked; for everything else the two are the same
    # minute. `publish_to_register` declines counter sales and repeats, so it is
    # safe to say twice, and the arrival sweep leans on that.
    elif new_status == OrderStatusEnum.ARRIVED_AT_POS:
        from app.services import order_service

        await order_service.publish_to_register(db, order)

    # The backstop, not the trigger. Arrival is what calls a driver now, and on
    # a batched zone the run has already gone out by the time anything is
    # packed. But an admin can mark an order packed that the sweep has not
    # reached — a branch with no terminal, a run whose window is still open and
    # a box the kitchen finished early — and that order still needs a van.
    # `assign_or_dispatch` returns untouched on anything already batched or
    # already booked, so on the ordinary path this is free. Nothing happens for
    # a third-party zone, exactly as before.
    elif new_status == OrderStatusEnum.PACKED:
        from app.services import batching_service

        await batching_service.assign_or_dispatch(db, order)

    elif new_status == OrderStatusEnum.CANCELLED:
        from app.services import batching_service, courier_service, lalamove_service

        delivery = await lalamove_service.get_delivery(db, order.id)
        if delivery is not None:
            # Off the run first, so a batch that is now empty does not go out
            # to collect nothing.
            await batching_service.cancel_assignment(db, delivery)
        await courier_service.cancel(db, order)

        # And closes on the register, if it ever reached one. Without this the
        # check stays open on the iPad forever: the cashier sees a live order
        # for a cancelled sale, can still add items to it, and it still counts
        # towards the day. Production already has cashier orders sitting
        # `cancelled` with `pos_status = active` from exactly this.
        #
        # Void rather than closed — closed means paid and finished, and this was
        # neither.
        if order.pos_status in _OPEN_ON_THE_REGISTER:
            order.pos_status = PosOrderStatusEnum.VOID.value

        # A cancelled order releases the stock it claimed at creation.
        await _move_stock(db, order, +1)

    # A cancellation taken back by a person. The only route here is the admin
    # console's `extra_from` (see `ADMIN_RECOVERABLE`) — nothing automatic can
    # reach it — and the one thing it owes is the reverse of the line above:
    # the cancellation put the stock back on the shelf, and the box did in fact
    # leave, so it has to come off again. Without this, every corrected
    # cancellation overstates stock by its own contents, permanently and
    # silently.
    #
    # Two things it deliberately does *not* undo, because neither can be undone
    # honestly here:
    #
    # * the register void. A settled check reopened is a till that no longer
    #   reconciles, which is a worse record than a voided check next to a
    #   delivered order.
    # * the refund. Money already returned is a fact at a bank, not a column.
    #   The warning below is the escalation — an order that reads `delivered`
    #   with a non-zero `refunded_amount` is a real loss and somebody has to
    #   look at it.
    elif (
        new_status == OrderStatusEnum.DELIVERED
        and previous == OrderStatusEnum.CANCELLED
    ):
        await _move_stock(db, order, -1)

    # Keyed on the money, not on which status the order is coming back from.
    # It used to ask `previous in _REFUNDABLE_ENDINGS`, which was the same
    # question only while that set held every status a refund could have
    # happened in. It no longer does — an `undelivered` order refunded under the
    # old rule is exactly the case this warning is for, and narrowing by the set
    # would step past it in silence. `refunded_amount` is the fact; ask it.
    if new_status == OrderStatusEnum.DELIVERED:
        refunded = order.refunded_amount or 0
        if refunded:
            logger.warning(
                "Order %s was moved from %s to delivered with %s already "
                "refunded. The refund is not reversed — the customer has both "
                "the money and the order.",
                order.order_number,
                getattr(previous, "value", previous),
                refunded,
            )

    # An order that is not going to arrive, that was paid for by card, gets the
    # money back without anybody pressing anything else.
    #
    # Cancellation alone. Automatic rather than a second admin step, because the
    # second step is the one that gets forgotten and the cost of forgetting it
    # is somebody who paid for nothing. The fees stay — see
    # `payment_service.refundable_amount` — since the van was booked and often
    # already drove.
    #
    # A failed handover is deliberately not here any more. It is not the same
    # fact to a customer: `cancelled` means the cake is not coming, and
    # `undelivered` means it did not get in today. Paying back the second one
    # automatically is what stopped anybody trying again.
    #
    # Never allowed to fail the transition. Cancelling is a fact about the
    # kitchen and refunding is a fact about a bank; holding the first hostage to
    # the second would leave a shop unable to stop making a cake because Stripe
    # was slow. `refund_order` swallows its own failures and returns zero, and
    # the order then shows as unrefunded for a person to deal with.
    if new_status in _REFUNDABLE_ENDINGS:
        from app.services import payment_service

        await payment_service.refund_order(db, order)
