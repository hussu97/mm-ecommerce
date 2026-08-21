"""
Which courier carries an order, and what happens when the first choice will not.

Three couriers now book themselves — Lalamove, noon Send and Slider — and the
code that triggers a dispatch (an order being packed, an admin pressing
re-dispatch, a batch window closing) should not have to know which. Everything
provider-shaped lives here; the call sites just ask for the order to go out.

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

**Slider is gated to one account while the pilot runs.** A `slider` zone is
carried by Slider only for a signed-in customer on `SLIDER_TRIAL_EMAILS`. For
everybody else the zone resolves to whoever carries them today — noon Send in
Sharjah, Lalamove everywhere else — which is *exactly* today's behaviour, since
every Slider zone was carved out of one of those two. That is what makes it safe
to publish the map before the courier is proven: the rollout is a no-op for
every customer except the trial account.

The list is deliberately not tied to `APP_ENV` or `SLIDER_ENV`. An
environment-shaped gate opens a trial to everyone the moment the environment
changes, which is the mistake the noon Send trial was written to avoid.
Emptying the list is how this trial ends — and that one setting ends both halves
at once, because it also grants the free delivery in `trial_customer`.

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
from app.services import (
    lalamove_service,
    noon_send_service,
    slider_service,
    trial_customer,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACKS",
    "books_itself",
    "cancel",
    "carrier_for",
    "may_be_carried_by",
    "dispatch",
    "estimate_for_point",
    "is_enabled",
    "may_use_noon_send",
]

LALAMOVE = FulfilmentProviderEnum.LALAMOVE.value
NOON_SEND = FulfilmentProviderEnum.NOON_SEND.value
SLIDER = FulfilmentProviderEnum.SLIDER.value
THIRD_PARTY = FulfilmentProviderEnum.THIRD_PARTY.value


def books_itself(provider: str | None) -> bool:
    """Whether this provider is one we dispatch over an API."""
    return provider in {LALAMOVE, NOON_SEND, SLIDER}


def is_enabled(provider: str | None) -> bool:
    """Whether the credentials for this provider are actually present."""
    if provider == NOON_SEND:
        return noon_send_service.is_enabled()
    if provider == LALAMOVE:
        return lalamove_service.is_enabled()
    if provider == SLIDER:
        return slider_service.is_enabled()
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


#: preferred courier -> the couriers the **automatic** routing may hand an order
#: to instead, in the order it tries them.
#:
#: Not the same thing as `delivery_polygons.alternate_providers`, and the two
#: are deliberately separate. Alternates are where a *person* may move a stuck
#: order; this is where the code sends one on its own, with nobody watching. A
#: zone can reasonably allow a manual move somewhere the dispatcher would never
#: go by itself.
#:
#: Written down rather than left implicit in `_dispatch_once` because two other
#: places have to ask the same question without an order in hand: whether a run
#: booked with one courier is a run this zone's orders can ever be on. See
#: `may_be_carried_by`.
FALLBACKS: dict[str, tuple[str, ...]] = {
    # noon Send inside Sharjah, Lalamove outside it — which between them is
    # every Slider zone, since all six were carved out of one or the other.
    SLIDER: (NOON_SEND, LALAMOVE),
    # The long-standing one: noon Send cap a run at 20 km, cannot cross an
    # emirate boundary, and can simply have nobody free.
    NOON_SEND: (LALAMOVE,),
}


def may_be_carried_by(preferred: str | None, courier: str | None) -> bool:
    """
    Whether an order in a `preferred` zone can end up on `courier` by itself.

    The question a shared run has to ask about a zone. A run is one booking with
    one courier, so a zone may only ride one its orders can actually be carried
    by — and since `126` that is no longer the same as "the zone names this
    courier": every order in a Slider zone but the pilot account's is handed
    straight back to Lalamove, so a Lalamove run is exactly where they belong.
    """
    return courier == preferred or courier in FALLBACKS.get(preferred or "", ())


def carrier_for(order: Order, delivery: OrderDelivery) -> tuple[str, str | None]:
    """
    Who will actually carry this order, and why it is not the zone's choice.

    The zone decides for two of the three couriers and nothing here changes
    that. For a `slider` zone it decides only for the trial account: everybody
    else is handed to whoever carried that ground before the zone existed —
    noon Send inside Sharjah, Lalamove outside it — which is what makes
    publishing the new map a no-op for every customer but one.

    **One function, and two callers that must not disagree.** `_dispatch_once`
    asks it to decide who to book, and `batching_service.reserve` asks it, at
    confirmation, to decide whether the order joins a shared run. If only the
    dispatcher knew about the swap, then every Dubai and Ajman order would skip
    its batch window — `reserve` keys off the literal string `lalamove` — and go
    out alone at roughly three times the courier cost, with nothing on any
    screen to say why. The gate is meant to change who carries the trial
    account's cake, not how the rest of Dubai is routed.

    The fallback is chosen from the **zone**, not from the address, and by name.
    `Sharjah Core` was carved out of `Sharjah Central`, which is noon Send's; the
    Ajman, Dubai and Umm al-Quwain Slider zones were carved out of Lalamove's.
    The zone name is therefore a statement about who used to carry this ground,
    which is exactly the question being asked — where the address's `city` is a
    string a customer typed and can say "Dubai" for a pin in Sharjah. The
    address is consulted only for a delivery row too old to carry a zone name.

    Returns the provider to book and, when it is not the zone's, the reason.
    """
    provider = delivery.provider
    if provider != SLIDER:
        return provider, None

    if not slider_service.is_enabled():
        # The same contract the other two have: an absent credential is a
        # fallback, never an outage.
        reason = "Slider is not configured"
    elif order.user_id is None:
        reason = "Slider is limited to signed-in pilot accounts"
    elif not trial_customer.is_trial_customer(order.user_id, order.email):
        reason = "Slider is limited to the pilot account"
    else:
        return SLIDER, None

    return (NOON_SEND if _was_noon_send_ground(order, delivery) else LALAMOVE), reason


def _was_noon_send_ground(order: Order, delivery: OrderDelivery) -> bool:
    """Whether this drop is in Sharjah, and so was noon Send's before Slider."""
    zone = (delivery.zone_name or "").strip().lower()
    if zone:
        return zone.startswith("sharjah")
    city = str((order.shipping_address_snapshot or {}).get("city") or "")
    return city.strip().lower().startswith("sharjah")


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
    carrier, gated = carrier_for(order, delivery)

    if carrier == SLIDER:
        allowed, refusal = await slider_service.may_serve(db, order)
        if allowed:
            result = await slider_service.dispatch_order(db, order)
            if result is not None and result.courier_order_id:
                return result
            refusal = result.last_error if result is not None else "Slider failed"
        # Slider publishes no serviceability endpoint, so an address outside
        # their area is only ever discovered here. Falling through rather than
        # reporting it is what stops that discovery stranding a paid order.
        carrier = NOON_SEND if _was_noon_send_ground(order, delivery) else LALAMOVE
        gated = refusal

    if gated is not None:
        # A swap the zone did not choose. The reason goes to the log and **not**
        # to `last_error`: a booking that succeeded is not a problem, and
        # `last_error` drives `needs_attention`, so filling it here would put
        # every gated order on the admin's needs-a-human list.
        #
        # And **not** to `original_provider` either, which is the same rule the
        # noon Send fallback below already follows. That column means "a human
        # moved this order", and three separate things read it that way: the
        # admin prints "moved from X" beside the courier, `allowed_targets`
        # treats it as the map's own choice when there is no zone to ask, and
        # `fulfilment_service._estimate` reads it as "this order was written
        # against a third-party zone" and answers tomorrow-before-10-PM instead
        # of an hour. Setting it here would put a hand-moved badge and a
        # next-day promise on every Dubai order in a Slider zone — which is to
        # say on almost all of them, since the gate hands almost all of them
        # back.
        #
        # What makes the swap visible is the same thing that makes the noon Send
        # one visible: the zone says one courier and the row says another, and
        # this line is the explanation.
        logger.info(
            "Order %s is in a %s zone and is going by %s: %s",
            order.order_number,
            delivery.provider,
            carrier,
            gated,
        )
        delivery.provider = carrier

    # The zone decides for the rest, and nothing else does. A `lalamove` zone is
    # never offered to noon Send — their fleet probably cannot reach it.
    if carrier != NOON_SEND:
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
    """
    Call off whichever courier is holding this order.

    **Never calls off a run.** A batched dispatch books one Lalamove order for
    up to fifteen stops and writes that single id onto every delivery in it, so
    `courier_order_id` on a batched order names a van carrying other people's
    cakes. Taking that id to `DELETE /v3/orders/{id}` would cancel every drop on
    it — four other customers' deliveries called off to stop one, silently,
    with each of those orders still reading `packed` and nothing coming.

    Such an order simply leaves the run instead. The van keeps its stop and
    arrives to find nothing there, which costs a wasted drop; the alternative
    costs four deliveries. `cancel_assignment` is what takes it off the batch,
    and the caller has already run it.
    """
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        return None

    # Imported here rather than at module scope: `batching_service` imports this
    # module at load time, and a top-level import would close the cycle.
    from app.services import batching_service

    run = await batching_service.shared_run_booking(db, delivery)
    if run is not None:
        logger.info(
            "Order %s was not cancelled with the courier — its booking %s is a "
            "shared run of %s stops. It has left the run instead.",
            order.order_number,
            delivery.courier_order_id,
            run.stop_count,
        )
        # Detached from the booking on our side so nothing later mistakes the
        # run's id for this order's own and tries again.
        delivery.courier_previous_status = delivery.courier_status
        delivery.courier_order_id = None
        delivery.courier_status = None
        delivery.share_link = None
        delivery.stop_id = None
        delivery.stop_sequence = None
        return delivery

    if delivery.provider == NOON_SEND:
        return await noon_send_service.cancel_delivery(db, order)
    if delivery.provider == SLIDER:
        return await slider_service.cancel_delivery(db, order)
    return await lalamove_service.cancel_delivery(db, order)


async def estimate_for_point(
    db: AsyncSession,
    provider: str | None,
    latitude: float,
    longitude: float,
    address: str | None = None,
    branch_id: uuid.UUID | None = None,
    zone_name: str | None = None,
):
    """
    What this zone's courier would charge us to reach this point.

    Never raises and never blocks a sale: this runs inside the pricing call the
    checkout makes on every pin move.

    A zone is estimated on its **own** courier, even when the per-order gate
    will later hand the delivery to somebody else — a Slider zone is quoted
    against Slider whether or not the customer is on the pilot list. The
    estimate describes the zone's economics, which is what the number is for;
    the per-order swap is recorded on the delivery row where it belongs.

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
    if provider == SLIDER and slider_service.is_enabled():
        return await slider_service.estimate_for_point(
            db,
            latitude,
            longitude,
            address,
            branch_id,
            # Slider prices a bike and a car differently and the rule that picks
            # between them turns on the drop's emirate — which, at a checkout
            # with nothing but a pin, only the polygon knows. Without it every
            # quote here would price the car, and a `Sharjah Core` order would
            # be shown a fare it will not be booked at.
            drop_emirate=zone_name,
        )
    return await lalamove_service.estimate_for_point(
        db, latitude, longitude, address, branch_id
    )
