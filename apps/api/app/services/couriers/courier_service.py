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

**Slider carries its own zones, for everyone.** A `slider` zone goes to Slider
whenever Slider is configured — routing is the map's business, like it is for
the other two, and which customer placed the order decides nothing. It briefly
did: while the integration was being proved on production a named allow-list was
the only thing that let a real order reach Slider's fleet. That list is gone.

One thing still stands between a `slider` zone and a Slider task, and it is the
same fallback the others have: an absent credential, or a refusal at booking,
must not strand a paid, packed order — so it drops to whoever carried that
ground before Slider, noon Send inside Sharjah and Lalamove outside it, and
records why.

Third-party zones fall through everything here untouched, exactly as before.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import trading_hours
from app.core.alerting import capture_issue
from app.models.delivery_polygon import FulfilmentProviderEnum
from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services import branch_hours_service
from app.services.couriers import lalamove_service, noon_send_service, slider_service

logger = logging.getLogger(__name__)

__all__ = [
    "FALLBACKS",
    "books_itself",
    "cancel",
    "carrier_for",
    "effective_provider",
    "may_be_carried_by",
    "dispatch",
    "estimate_for_point",
    "is_enabled",
    "may_use_noon_send",
]

LALAMOVE = FulfilmentProviderEnum.LALAMOVE.value
NOON_SEND = FulfilmentProviderEnum.NOON_SEND.value
SLIDER = FulfilmentProviderEnum.SLIDER.value
SLIDER_BIKE = FulfilmentProviderEnum.SLIDER_BIKE.value
SLIDER_CAR = FulfilmentProviderEnum.SLIDER_CAR.value
THIRD_PARTY = FulfilmentProviderEnum.THIRD_PARTY.value

#: The bare legacy value and the two tier-pinned ones. A zone naming any of them
#: is Slider's — the tier only decides which vehicle, not which courier — so the
#: routing here treats them together and `slider_service` is the one that cares
#: about bike versus car.
SLIDER_PROVIDERS = frozenset({SLIDER, SLIDER_BIKE, SLIDER_CAR})

#: How long to wait between re-tries of a single order's dispatch, one rung per
#: attempt. A failure that outlives the last rung stops retrying and goes on the
#: admin's needs-a-human list. (This used to live in `batching_service`, shared
#: with the batch-retry ladder; batching is gone and the single-order retry keeps
#: it here.)
RETRY_BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=45),
)


def books_itself(provider: str | None) -> bool:
    """Whether this provider is one we dispatch over an API."""
    return provider in {LALAMOVE, NOON_SEND} or provider in SLIDER_PROVIDERS


def is_enabled(provider: str | None) -> bool:
    """Whether the credentials for this provider are actually present."""
    if provider == NOON_SEND:
        return noon_send_service.is_enabled()
    if provider == LALAMOVE:
        return lalamove_service.is_enabled()
    if provider in SLIDER_PROVIDERS:
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
    # noon Send inside Sharjah, Lalamove outside it — where a Slider zone falls
    # when Slider is unconfigured or refuses at booking. The tier does not change
    # the fallback: neither a bike nor a car can be dispatched without Slider, so
    # both go to the courier that carried that ground before. (Upgrading a bike
    # to a car is a manual, human-only move — `fulfilment_reassignment` — not an
    # automatic fallback the dispatcher takes on its own.)
    SLIDER: (NOON_SEND, LALAMOVE),
    SLIDER_BIKE: (NOON_SEND, LALAMOVE),
    SLIDER_CAR: (NOON_SEND, LALAMOVE),
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

    The zone decides for all three couriers now, and this is the thin adapter
    that reads an `Order` and an `OrderDelivery` and asks `effective_provider`
    the question in strings. The only time the answer is not the zone's own is a
    Slider zone with Slider unconfigured, which falls back to whoever carried
    that ground before — noon Send inside Sharjah, Lalamove outside it.

    That fallback is chosen from the **zone**, not the address, and by name.
    `Sharjah Core` was carved out of `Sharjah Central`, which is noon Send's; the
    Ajman, Dubai and Umm al-Quwain Slider zones were carved out of Lalamove's.
    The zone name is a statement about who used to carry this ground, which is
    exactly the question — where the address's `city` is a string a customer
    typed and can say "Dubai" for a pin in Sharjah. The address is consulted only
    for a delivery row too old to carry a zone name.

    Returns the provider to book and, when it is not the zone's, the reason.

    **One decision, asked in more than one place that must not disagree.**
    `_dispatch_once` asks who to book, and the fare quote and order-creation stamp
    have to reach the same verdict — a row stamped for one courier and dispatched
    on another parks the wrong error and prints the wrong carrier. So the core
    lives in `effective_provider`, which knows nothing about an `Order`, and this
    reads the two rows and calls it.
    """
    return effective_provider(
        delivery.provider,
        delivery.zone_name,
        # Read tolerantly with `getattr`: a non-Slider zone returns from
        # `effective_provider` before `city` is looked at, and some callers pass
        # an order carrying only an id and a number. Keeping the read lazy keeps
        # that true.
        city=str(
            (getattr(order, "shipping_address_snapshot", None) or {}).get("city") or ""
        ),
    )


def effective_provider(
    zone_provider: str | None,
    zone_name: str | None,
    *,
    city: str | None = None,
) -> tuple[str, str | None]:
    """
    The courier a zone actually resolves to, and why it is not the zone's own.

    Everything a Slider zone's fallback needs, expressed in strings rather than
    rows, so the one decision can be asked at dispatch (`carrier_for`), at the
    fare quote (`estimate_for_point`) and at order creation — where the answer
    must agree or the customer is shown one courier, charged against a second
    and delivered by a third.

    A non-Slider zone passes straight through: the map decides for the other two
    couriers and nothing here changes that. A Slider zone is Slider's whenever
    Slider is configured; only an absent credential falls back — to noon Send
    inside Sharjah, Lalamove outside it — the same "unconfigured is a fallback,
    never an outage" contract the other two couriers have.

    `city` is consulted only for that fallback on a Slider zone with no name to
    read, which in practice is a delivery row too old to carry one: the zone name
    is a statement about who used to carry this ground and is trusted first,
    where the address's `city` is a string a customer typed and can say "Dubai"
    for a pin in Sharjah. Every zone name begins with its emirate by
    construction, so the fare quote — which has a name but no address — needs no
    `city` at all.
    """
    if zone_provider not in SLIDER_PROVIDERS:
        return zone_provider, None

    if slider_service.is_enabled():
        # The zone's own tier, unchanged — `slider_bike` stays `slider_bike`. The
        # vehicle is `slider_service`'s business; this only decides the courier.
        return zone_provider, None

    # The same contract the other two have: an absent credential is a fallback,
    # never an outage.
    return (
        NOON_SEND if _is_sharjah_ground(zone_name, city) else LALAMOVE
    ), "Slider is not configured"


def _was_noon_send_ground(order: Order, delivery: OrderDelivery) -> bool:
    """Whether this drop is in Sharjah, and so was noon Send's before Slider."""
    return _is_sharjah_ground(
        delivery.zone_name,
        str((order.shipping_address_snapshot or {}).get("city") or ""),
    )


def _is_sharjah_ground(zone_name: str | None, city: str | None) -> bool:
    """
    Whether this drop is in Sharjah, and so was noon Send's before Slider.

    The zone name wins where there is one — it is the statement about who used
    to carry this ground, which is exactly the question — and the customer-typed
    `city` is the fallback for a row too old to carry a name.
    """
    zone = (zone_name or "").strip().lower()
    if zone:
        return zone.startswith("sharjah")
    return (city or "").strip().lower().startswith("sharjah")


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


def _note_auto_fallback(
    order: Order,
    delivery: OrderDelivery,
    *,
    zone_provider: str | None,
    carrier: str,
    reason: str | None,
) -> None:
    """Record and raise the alarm that automatic dispatch left the zone's courier.

    The fallback booking still succeeds, so this must **not** reach `last_error`,
    which drives `needs_attention` — it would put every gated order on the
    admin's needs-a-human list. It goes to two places that a success does not
    poison: `fallback_reason`, the durable copy of the log line, and a
    fingerprinted Sentry warning.

    Both exist because this was silent. A Slider account whose prepaid wallet
    could not cover a real fare returned 402 on every booking; every Slider order
    dropped to Lalamove; `last_error` was cleared by the Lalamove booking that
    worked; and nothing said Slider had stopped carrying anything. The log line
    alone was an INFO nobody reads. The fingerprint groups a recurring condition
    into one alertable issue rather than one event per order — "Slider is falling
    back to Lalamove" is the thing worth waking up for.
    """
    delivery.fallback_reason = reason
    logger.info(
        "Order %s is in a %s zone and is going by %s: %s",
        order.order_number,
        zone_provider,
        carrier,
        reason,
    )
    capture_issue(
        f"Courier auto-fallback: {zone_provider} → {carrier} ({reason})",
        level="warning",
        fingerprint=["courier-auto-fallback", zone_provider or "?", carrier],
        tags={
            "order_number": order.order_number,
            "zone_provider": zone_provider or "",
            "carrier": carrier,
        },
    )


async def _dispatch_once(
    db: AsyncSession, order: Order, delivery: OrderDelivery
) -> OrderDelivery | None:
    """One attempt, on whichever courier ends up carrying it."""
    # Cleared before the attempt, so a re-dispatch that now succeeds on the
    # zone's own courier leaves no stale "fell back" note behind. Set again below
    # only if this attempt actually falls back.
    delivery.fallback_reason = None
    carrier, gated = carrier_for(order, delivery)

    if carrier in SLIDER_PROVIDERS:
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
        # `_note_auto_fallback` is the explanation — a durable `fallback_reason`
        # and a Sentry warning, because the log line alone let an unfunded Slider
        # wallet route every order to Lalamove with nothing red anywhere.
        _note_auto_fallback(
            order,
            delivery,
            zone_provider=delivery.provider,
            carrier=carrier,
            reason=gated,
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
    # The reason goes to `fallback_reason` and a Sentry warning rather than to
    # `last_error`, because a booking that succeeded is not a problem:
    # `last_error` drives `needs_attention` and filling it here would put every
    # fallback on the admin's needs-a-human list. A `noon_send` zone showing a
    # `lalamove` delivery is the signal, and `_note_auto_fallback` is the
    # explanation.
    _note_auto_fallback(
        order, delivery, zone_provider=NOON_SEND, carrier=LALAMOVE, reason=reason
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
        # Imported here rather than at the top: it imports this module.
        from app.services.orders import order_service

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
    sched = (
        await branch_hours_service.schedule(db, order.branch_id)
        if getattr(order, "branch_id", None)
        else None
    )
    window = branch_hours_service.effective_window(
        sched, trading_hours.local(datetime.now(timezone.utc)).date()
    )
    opens_at, closes_at = window if window else ("00:00", "23:59")
    # `or 0` because the column default is applied by the database at INSERT: a
    # delivery row built in memory and not yet flushed still reads None, and a
    # dispatch can be attempted on one — the checkout writes the row and the
    # register accepts the order inside the same request.
    delivery.dispatch_attempts = (delivery.dispatch_attempts or 0) + 1
    delivery.next_attempt_at = _retry_at(
        delivery, opens_at=opens_at, closes_at=closes_at
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

    Two things end it. Running out of rungs (`RETRY_BACKOFF`), which means the
    failure has outlived the kind of problem that fixes itself. And landing after
    the kitchen has shut — the driver collects from a physical counter, and a
    booking made at 00:05 for something that failed at 23:00 sends somebody to a
    dark shop.
    """
    if delivery.dispatch_attempts > len(RETRY_BACKOFF):
        return None
    moment = now or datetime.now(timezone.utc)
    when = moment + RETRY_BACKOFF[delivery.dispatch_attempts - 1]
    if not trading_hours.is_open(when, opens_at, closes_at):
        return None
    return when


#: The statuses an order can be in and still want a driver. Anything else has
#: either already travelled or stopped being a delivery. `undelivered` is
#: deliberately not here: a failed handover ends in a refund conversation a
#: person starts, not a second unattended van for an order somebody wrote off.
_RETRYABLE_STATUSES = {
    OrderStatusEnum.CONFIRMED,
    OrderStatusEnum.ARRIVED_AT_POS,
    OrderStatusEnum.PACKED,
}


async def retry_failed_dispatches(db: AsyncSession, *, limit: int = 20) -> list:
    """
    Re-dispatch every order whose retry has fallen due.

    An order that failed to book a courier is left with a `next_attempt_at` and
    no `courier_order_id` by `_record_outcome`; the retry ladder (`_retry_at`)
    set the time. This sweep asks again rather than leaving it for a human to
    spot a red box on an admin screen — MM-20260815-001 waited six hours on an
    error a deploy had fixed in twenty-six minutes. It runs on its own session
    in the delivery scheduler.
    """
    now = datetime.now(timezone.utc)
    due = (
        (
            await db.execute(
                select(OrderDelivery)
                .where(
                    OrderDelivery.next_attempt_at.isnot(None),
                    OrderDelivery.next_attempt_at <= now,
                    OrderDelivery.courier_order_id.is_(None),
                )
                .order_by(OrderDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    retried: list = []
    for delivery in due:
        order = await db.get(Order, delivery.order_id)
        if order is None:  # pragma: no cover — FK is RESTRICT
            delivery.next_attempt_at = None
            continue
        # A settled order stops asking — a cake going out for a refunded order is
        # worse than a late one.
        if order.status not in _RETRYABLE_STATUSES:
            logger.info(
                "Order %s is %s; abandoning its retry",
                order.order_number,
                order.status.value,
            )
            delivery.next_attempt_at = None
            await db.commit()
            continue

        # Cleared before the attempt: `dispatch` writes a fresh `next_attempt_at`
        # if it fails again, and leaving the old one would have the sweep pick the
        # same order up on the next tick.
        delivery.next_attempt_at = None
        try:
            await dispatch(db, order)
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "Retry for order %s blew up; it will be left for a human",
                order.order_number,
            )
        await db.commit()
        retried.append(delivery.id)
    return retried


async def cancel(db: AsyncSession, order: Order) -> OrderDelivery | None:
    """
    Call off whichever courier is holding this order.

    Every booking is this order's own — one dispatch, one courier order id — so
    calling it off with the provider is always the right thing.
    """
    delivery = await lalamove_service.get_delivery(db, order.id)
    if delivery is None:
        return None

    if delivery.provider == NOON_SEND:
        return await noon_send_service.cancel_delivery(db, order)
    if delivery.provider in SLIDER_PROVIDERS:
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

    The courier asked is the one that will actually carry the order:
    `effective_provider` resolves the zone first, so a Slider zone with Slider
    unconfigured is quoted against its fallback — noon Send inside Sharjah,
    Lalamove outside it — rather than against a Slider fare endpoint nobody will
    be booked at. `zone_name` carries the emirate by construction, which is all
    that fallback needs to tell Sharjah ground from the rest, so no address is
    required here even though `carrier_for` reads one at dispatch.

    Everything else — including third-party zones and pins outside every zone —
    is still quoted against Lalamove, unchanged. Those quotes are not used to
    dispatch anything; they are the evidence for whether a manually-served area
    could be served by a courier instead, and that question stops being
    answerable the moment we stop asking it.
    """
    # Resolved before the dispatch below, so a Slider zone with Slider
    # unconfigured is quoted against the courier that will actually carry it
    # rather than a fare endpoint the order will never reach.
    provider, _ = effective_provider(provider, zone_name)

    if provider == NOON_SEND and noon_send_service.is_enabled():
        return await noon_send_service.estimate_for_point(
            db, latitude, longitude, address, branch_id
        )
    if provider in SLIDER_PROVIDERS and slider_service.is_enabled():
        return await slider_service.estimate_for_point(
            db,
            latitude,
            longitude,
            address,
            branch_id,
            # Slider prices a bike and a car differently. A tier-pinned zone names
            # the vehicle outright (`slider_bike`/`slider_car`), so quote it as
            # named; a bare legacy `slider` zone passes no tier and it is computed
            # from the drop's emirate, which — at a checkout with nothing but a
            # pin — only the polygon knows.
            drop_emirate=zone_name,
            vehicle=slider_service.vehicle_for_provider(provider),
        )
    return await lalamove_service.estimate_for_point(
        db, latitude, longitude, address, branch_id
    )
