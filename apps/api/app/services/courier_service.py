"""
Which courier carries an order, and what happens when the first choice will not.

Two couriers now book themselves — Lalamove and noon Send — and the code that
triggers a dispatch (an order being packed, an admin pressing re-dispatch, a
batch window closing) should not have to know which. Everything provider-shaped
lives here; the call sites just ask for the order to go out.

**noon Send is the preferred courier where a zone names it**, because on a bike
it is cheaper than Lalamove at every distance it can reach, surge included: AED
12 flat to 10 road km against Lalamove's `17 + 0.70/km`. Two things stand
between a `noon_send` zone and an actual noon Send task.

*The trial list.* noon Send only carries orders placed by a signed-in customer
on `TRIAL_CUSTOMER_EMAILS`. Everybody else in the zone is carried by Lalamove
and notices nothing. The list applies in every environment and is the only
thing gating noon Send — deliberately not tied to `APP_ENV` or to
`NOON_SEND_ENV`, because production currently points at noon's *staging* fleet
and an environment-shaped gate would have opened the trial to every customer
the moment it did. Emptying the list is how the trial ends.

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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.services import lalamove_service, noon_send_service, trial_customer

logger = logging.getLogger(__name__)

__all__ = [
    "books_itself",
    "cancel",
    "dispatch",
    "estimate_for_point",
    "is_enabled",
    "is_trial_run",
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

    The reason is returned rather than logged and dropped, because "this went
    Lalamove even though the zone says noon Send" is otherwise invisible and
    looks like a bug.
    """
    if not noon_send_service.is_enabled():
        return False, "noon Send is not configured"

    if not trial_customer.emails():
        # Deliberately open: emptying the list is how the trial ends.
        return True, None
    if order.user_id is None:
        return False, "noon Send is limited to signed-in trial accounts"
    if not trial_customer.is_trial_customer(order.user_id, order.email):
        return False, "noon Send is limited to the trial account"
    return True, None


def is_trial_run(order: Order) -> bool:
    """
    Whether this order should be offered to noon Send regardless of its zone.

    Public because batching has to ask it as well: a trial order must not join a
    Lalamove run, since a run is booked directly against Lalamove and never
    passes back through `dispatch`.
    """
    return (
        noon_send_service.is_enabled()
        and noon_send_service.trial_pickup() is not None
        and trial_customer.is_trial_customer(
            getattr(order, "user_id", None), getattr(order, "email", None)
        )
    )


# ── dispatch ──────────────────────────────────────────────────────────────────


async def dispatch(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Send one order out, on whichever courier its zone named.

    Returns the delivery row, whatever happened to it. A failure is written to
    `last_error` and returned quietly rather than raised: the order is already
    paid for, and refusing to change its status because a courier is unreachable
    helps nobody.
    """
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None or not books_itself(delivery.provider):
        return delivery

    # A trial order tries noon Send wherever it is going, not only where the map
    # says so. The map is drawn for the real fleet out of the Sharjah kitchen;
    # the staging fleet serves three Dubai outlets and cannot cross an emirate
    # boundary, so a trial run has to leave from Dubai and arrive in Dubai —
    # which is a `lalamove` zone on every map we have. Without this the trial
    # account could only ever exercise noon Send by ordering into a zone the
    # staging fleet cannot serve.
    #
    # Redrawing the map for a test fleet would be the wrong trade: the polygons
    # describe the business, and this describes one account.
    if delivery.provider != NOON_SEND and not is_trial_run(order):
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
