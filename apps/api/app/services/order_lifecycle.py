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
    # An ending, not a detour. This used to lead back into the journey —
    # `packed` again, then `out_for_delivery` when a new rider collected — on
    # the reasoning that the cake exists and is paid for, so the usual answer is
    # a second attempt.
    #
    # The shop's answer is that it is not. `undelivered` is what a person writes
    # down when a handover has definitively failed; it is cancellation after the
    # box was made, and treating it as recoverable meant a driver could be sent
    # again for something already written off, automatically, by a sweep. What
    # follows is a refund conversation, not another van.
    #
    # Only the two money outcomes remain, and they are the same two `cancelled`
    # now carries. Neither returns the order to fulfilment: they record what
    # happened to the payment for an order that is not going anywhere.
    OrderStatusEnum.UNDELIVERED: {
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


#: The two ways an order stops being deliverable with the customer's money still
#: in our account. `refunded` and `disputed` are not here: by the time an order
#: reaches either, the money has already moved.
_REFUNDABLE_ENDINGS = {
    OrderStatusEnum.CANCELLED,
    OrderStatusEnum.UNDELIVERED,
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

    token = _GATED.set(True)
    try:
        order.status = new_status
    finally:
        _GATED.reset(token)

    await _consequences(db, order, new_status)
    return True


async def _consequences(
    db: AsyncSession, order: Order, new_status: OrderStatusEnum
) -> None:
    """
    What arriving at a status makes happen, whoever brought the order there.

    These used to live in `order_service.update_status`, which meant they fired
    only when an admin moved the order: a courier webhook marking an order
    undelivered skipped the refund while its email promised one. Keyed off the
    transition, the machinery is identical for the console, a webhook, the
    checkout and the till.

    The service imports are deferred: this module sits underneath
    `order_service` and the couriers, and importing them at the top would close
    the cycle they already thread carefully around.
    """
    # Confirming by hand is a confirmation like any other — a bank transfer
    # reconciled in the morning, or a declined card the customer paid another
    # way. The register hears about the order the moment the money is
    # acknowledged, whichever route acknowledged it. (`publish_to_register`
    # itself declines counter sales and repeats — it is safe to say twice.)
    if new_status == OrderStatusEnum.CONFIRMED:
        from app.services import order_service

        await order_service.publish_to_register(db, order)

    # The backstop, not the trigger. Acceptance on the register is what calls a
    # driver now — early enough that the drive overlaps the prep rather than
    # queueing behind it. But not every order is accepted on a register: a
    # branch with no terminal receiving online orders has no acceptance event at
    # all, and an admin marking such an order packed in the console must still
    # get a van to the door. `assign_or_dispatch` returns untouched on anything
    # already batched or already booked, so on the ordinary path this is free.
    # Nothing happens for a third-party zone, exactly as before.
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

        # A cancelled order releases the stock it claimed at creation. Only a
        # website order claimed any: checkout decrements `stock_quantity` when
        # the order is written, while a counter sale depletes recipe
        # ingredients at close instead. Restocking a counter sale here would
        # invent stock it never took — which is what cancelling one from the
        # console used to do.
        if order.source == OrderSourceEnum.ONLINE.value:
            for item in order.items:
                if item.product_id:
                    await db.execute(
                        sql_update(Product)
                        .where(
                            Product.id == item.product_id,
                            Product.is_stock_product.is_(True),
                        )
                        .values(stock_quantity=Product.stock_quantity + item.quantity)
                        .execution_options(synchronize_session=False)
                    )

    # An order that is not going to arrive, that was paid for by card, gets the
    # money back without anybody pressing anything else.
    #
    # Both endings, because they are the same fact to a customer: the cake is
    # not coming. Automatic rather than a second admin step, because the second
    # step is the one that gets forgotten and the cost of forgetting it is
    # somebody who paid for nothing. The fees stay — see
    # `payment_service.refundable_amount` — since the van was booked and, on an
    # undelivered order, usually already drove.
    #
    # Never allowed to fail the transition. Cancelling is a fact about the
    # kitchen and refunding is a fact about a bank; holding the first hostage to
    # the second would leave a shop unable to stop making a cake because Stripe
    # was slow. `refund_order` swallows its own failures and returns zero, and
    # the order then shows as unrefunded for a person to deal with.
    if new_status in _REFUNDABLE_ENDINGS:
        from app.services import payment_service

        await payment_service.refund_order(db, order)
