"""
Which courier carries an order, and what happens when the first choice will not.

Two couriers now book themselves — Lalamove and noon Send — and the code that
triggers a dispatch (an order being packed, an admin pressing re-dispatch, a
batch window closing) should not have to know which. Everything provider-shaped
lives here; the call sites just ask for the order to go out.

**noon Send is the preferred courier where a zone names it**, because on a bike
it is cheaper than Lalamove at every distance it can reach, surge included: AED
12 flat to 10 road km against Lalamove's `17 + 0.70/km`. Every order in such a
zone is offered to them — which customer placed it, and whether they were signed
in at all, decides nothing. It briefly did: while the integration was being
proved on production, a named allow-list was the only thing that let a real
order reach noon's fleet. That list is gone, and routing is the map's business
again.

One thing still stands between a `noon_send` zone and an actual noon Send task.
*The fallback.* noon Send caps a run at 15 km, cannot cross an emirate boundary,
and can simply have nobody free. Any of those is a refusal, and a refusal must
not strand a paid, packed order — so it books Lalamove instead and records why.
The reverse does not apply: a Lalamove zone is never handed to noon Send,
because noon Send probably cannot reach it.

Third-party zones fall through everything here untouched, exactly as before.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.models.branch import Branch
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.services import lalamove_service, noon_send_service

logger = logging.getLogger(__name__)

__all__ = [
    "books_itself",
    "cancel",
    "dispatch",
    "estimate_for_point",
    "is_enabled",
    "may_use_noon_send",
]

LALAMOVE = FulfilmentProviderEnum.LALAMOVE.value
NOON_SEND = FulfilmentProviderEnum.NOON_SEND.value
THIRD_PARTY = FulfilmentProviderEnum.THIRD_PARTY.value


def books_itself(provider: str | None) -> bool:
    """Whether this provider is one we dispatch over an API."""
    return provider in {LALAMOVE, NOON_SEND}


def is_enabled(provider: str | None) -> bool:
    """Whether the credentials for this provider are actually present."""
    if provider == NOON_SEND:
        return noon_send_service.is_enabled()
    if provider == LALAMOVE:
        return lalamove_service.is_enabled()
    return False


def may_use_noon_send(order: Order) -> tuple[bool, str | None]:
    """
    Whether this particular order is allowed onto noon Send, and why not.

    Only one thing can refuse now — no credentials — but the shape is kept:
    the reason is returned rather than logged and dropped, because "this went
    Lalamove even though the zone says noon Send" is otherwise invisible and
    looks like a bug. It is also where a future rule would go, and one caller
    already knows how to report whatever this says.
    """
    if not noon_send_service.is_enabled():
        return False, "noon Send is not configured"
    return True, None


# ── dispatch ──────────────────────────────────────────────────────────────────


async def dispatch(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Send one order out, on whichever courier its zone named.

    Returns the delivery row, whatever happened to it. A failure is written to
    `last_error` and returned quietly rather than raised: the order is already
    paid for, and refusing to change its status because a courier is unreachable
    helps nobody.

    **This is also the one place that records whether it worked.** The provider
    modules write `last_error` and know nothing about retries; the ladder is
    applied here, once, on the way out — so a rung cannot be skipped by whichever
    of the two providers happened to answer, and the noon Send → Lalamove
    fallback counts as the single attempt it is rather than two.
    """
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None or not books_itself(delivery.provider):
        return delivery

    return await _record_outcome(db, order, await _dispatch_once(db, order, delivery))


async def _dispatch_once(
    db: AsyncSession, order: Order, delivery: OrderDelivery
) -> OrderDelivery | None:
    """One attempt, on whichever courier ends up carrying it."""
    # The zone decides, and nothing else does. A `lalamove` zone is never
    # offered to noon Send — their fleet probably cannot reach it.
    if delivery.provider != NOON_SEND:
        return await lalamove_service.dispatch_order(db, order)

    allowed, reason = may_use_noon_send(order)
    if allowed:
        in_range, out_of_range = await noon_send_service.may_serve(db, order)
        if in_range:
            result = await noon_send_service.dispatch_order(db, order)
            if result is not None and result.courier_order_id:
                return result
            reason = result.last_error if result is not None else "noon Send failed"
        else:
            reason = out_of_range

    # Either noon Send would not take it or was not allowed to. The order still
    # has to travel, so it travels with the courier that has no such limits, and
    # the row records honestly who ended up carrying it.
    #
    # The reason goes to the log rather than to `last_error`, because a booking
    # that succeeded is not a problem: `last_error` drives `needs_attention` and
    # filling it here would put every fallback on the admin's needs-a-human
    # list. A `noon_send` zone showing a `lalamove` delivery is the signal, and
    # this line is the explanation.
    logger.info(
        "Order %s was offered to noon Send and is going by Lalamove: %s",
        order.order_number,
        reason,
    )
    delivery.provider = LALAMOVE
    return await lalamove_service.dispatch_order(db, order)


# ── retry ─────────────────────────────────────────────────────────────────────


async def _record_outcome(
    db: AsyncSession, order: Order, delivery: OrderDelivery | None
) -> OrderDelivery | None:
    """
    Whether anything will happen to this order on its own, written down.

    Called on the way out of every dispatch, including the ones that worked. A
    success that did not clear the counter would have the next failure start
    three rungs up the ladder and give up almost immediately.
    """
    if delivery is None:
        return None

    if delivery.courier_order_id and not delivery.last_error:
        delivery.dispatch_attempts = 0
        delivery.next_attempt_at = None
        # Imported here rather than at the top: both of these import this
        # module.
        from app.services import batching_service, order_service

        # This box has a van of its own now, so it is not riding the run any
        # more — and the run has to be told, because until it is it still counts
        # this order as a stop. An admin pressing Dispatch now on one order out
        # of a pending run left the Runs tab claiming a drop that was already
        # being driven across town, and left the run itself scheduled to go out
        # and collect it. It corrected itself only at the window's close, hours
        # later, when the booking guard in `_ready_deliveries` found nothing to
        # send.
        #
        # `cancel_assignment` is the same call an order cancellation makes and
        # means the same thing here: off the run, stop count re-counted, and the
        # run cancelled outright if this was the last thing on it. A run that has
        # already left is left alone — a driver carrying the rest of it is not
        # something this can rewrite.
        await batching_service.cancel_assignment(db, delivery)

        # A driver has been called for this box, which is the closest thing to
        # "packed" that anybody now says out loud — the press that used to say
        # it is gone from the register.
        await order_service.stamp_packed(
            db, order, note=f"{delivery.provider} booking accepted"
        )
        return delivery

    if not delivery.last_error:
        # Neither booked nor failed: the provider declined to act at all, which
        # is what a re-dispatch of an order already out with a driver does.
        # Nothing to schedule and nothing to clear.
        return delivery

    # The branch's own hours, not a default pair, because the whole point of the
    # kitchen-hours cutoff is that this particular counter will be dark. A
    # primary-key get rather than `resolve_pickup`: the zone's fallback logic is
    # for deciding where a driver collects from, and all this needs is two
    # strings off a row the session has usually already loaded.
    branch = (
        await db.get(Branch, order.branch_id)
        if getattr(order, "branch_id", None)
        else None
    )
    # `or 0` because the column default is applied by the database at INSERT: a
    # delivery row built in memory and not yet flushed still reads None, and a
    # dispatch can be attempted on one — the checkout writes the row and the
    # register accepts the order inside the same request.
    delivery.dispatch_attempts = (delivery.dispatch_attempts or 0) + 1
    delivery.next_attempt_at = _retry_at(
        delivery,
        opens_at=branch.opening_from if branch else "00:00",
        closes_at=branch.opening_to if branch else "23:59",
    )
    if delivery.next_attempt_at is None:
        logger.warning(
            "Dispatch for delivery %s failed after %s attempt(s) and needs a human: %s",
            delivery.id,
            delivery.dispatch_attempts,
            delivery.last_error,
        )
    else:
        logger.info(
            "Dispatch for delivery %s failed (attempt %s); retrying at %s",
            delivery.id,
            delivery.dispatch_attempts,
            delivery.next_attempt_at.isoformat(),
        )
    return delivery


def _retry_at(
    delivery: OrderDelivery,
    *,
    now: datetime | None = None,
    opens_at: str = "00:00",
    closes_at: str = "23:59",
) -> datetime | None:
    """
    When to ask again, or None for "not on its own".

    Two things end it, both borrowed from the batch ladder because they are the
    same two facts. Running out of rungs, which means the failure has outlived
    the kind of problem that fixes itself. And landing after the kitchen has
    shut — the driver collects from a physical counter, and a booking made at
    00:05 for something that failed at 23:00 sends somebody to a dark shop.

    The ladder itself is `batching_service.RETRY_BACKOFF`, imported here rather
    than copied: a second tuple that drifted by five minutes would be invisible
    until somebody compared two orders that failed the same way. Imported
    *inside the function* because `batching_service` imports this module at the
    top of its own file, and closing that cycle at import time is a crash on
    boot rather than a lint warning.
    """
    from app.services.batching_service import RETRY_BACKOFF as LADDER

    if delivery.dispatch_attempts > len(LADDER):
        return None
    moment = now or datetime.now(timezone.utc)
    when = moment + LADDER[delivery.dispatch_attempts - 1]
    if not trading_hours.is_open(when, opens_at, closes_at):
        return None
    return when


async def cancel(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """Call off whichever courier is holding this order."""
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        return None
    if delivery.provider == NOON_SEND:
        return await noon_send_service.cancel_delivery(db, order)
    return await lalamove_service.cancel_delivery(db, order)


async def estimate_for_point(
    db: AsyncSession,
    provider: str | None,
    latitude: float,
    longitude: float,
    address: str | None = None,
    branch_id: uuid.UUID | None = None,
):
    """
    What this zone's courier would charge us to reach this point.

    Never raises and never blocks a sale: this runs inside the pricing call the
    checkout makes on every pin move.

    A noon Send zone is estimated on noon Send's rate card even when the
    allow-list will later send the order to Lalamove. The estimate describes the
    zone's economics, which is what the number is for; the per-order swap is
    recorded on the delivery row where it belongs.

    Everything else — including third-party zones and pins outside every zone —
    is still quoted against Lalamove, unchanged. Those quotes are not used to
    dispatch anything; they are the evidence for whether a manually-served area
    could be served by a courier instead, and that question stops being
    answerable the moment we stop asking it.
    """
    if provider == NOON_SEND and noon_send_service.is_enabled():
        return await noon_send_service.estimate_for_point(
            db, latitude, longitude, address, branch_id
        )
    return await lalamove_service.estimate_for_point(
        db, latitude, longitude, address, branch_id
    )
