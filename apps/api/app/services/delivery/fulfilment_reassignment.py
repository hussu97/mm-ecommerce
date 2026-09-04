"""
Moving one order from the courier its zone chose to a courier that will carry it.

A courier declining a job is ordinary. A courier accepting one and then going
quiet — a booking sat at `ASSIGNING_DRIVER` for forty minutes, a task nobody
picks up — is not rare either, and until this module existed the shop's only
answer was a phone call. There was exactly one escape in the whole system:
a packed third-party order onto Lalamove, in that direction and no other.

This is that escape made general, and bounded by policy rather than by which
direction happened to get built first. Two questions, answered in two places:

* **Where may this order go?** The map. Each zone names a preferred courier —
  the column it always had — and now also the alternates its orders may be moved
  to. So "in Dubai a Lalamove order may not go to noon Send" is a row, not a
  rule somebody remembers. See `DEFAULT_ALTERNATES`.
* **May this order move at all?** `refuse`, below. Status, refunds, whether a
  rider is already holding the box, whether one is already on their way to
  collect it.

Nothing here books, cancels or prices anything itself. Every side effect is
delegated — `courier_service` to cancel, `lalamove_service` / `noon_send_service`
to book, `batching_service` to leave a run, `driver_assignment` to close a stint,
`email_service` to make good what the move leaves owing. A second copy of any of
those would be a second thing to keep in step with the first, and the first is
where the money is.

**The customer's delivery fee never changes.** They paid the fee their zone
publishes and that is what they paid. What moves is our cost, and therefore the
margin — which is the number the person pressing the button is accepting.

**The customer is never told which courier carries their order.** That rule is
older than this module and this module does not weaken it: nothing here writes
anything the storefront can read except through `fulfilment_service`, which has
nowhere to put a provider name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ServiceUnavailableError
from app.models.delivery_polygon import (
    DEFAULT_ALTERNATES,
    DeliveryPolygon,
    FulfilmentProviderEnum,
)
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery, is_collected, is_failed
from app.services import email_service
from app.services.couriers import (
    courier_service,
    lalamove_service,
    noon_send_service,
    slider_service,
)
from app.services.delivery import (
    batching_service,
    delivery_zone_service,
    driver_assignment,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Exposure",
    "Options",
    "Quote",
    "Target",
    "abandon_booking",
    "allowed_targets",
    "exposure_of",
    "move",
    "options_for",
    "quote",
    "refuse",
]

LALAMOVE = FulfilmentProviderEnum.LALAMOVE.value
NOON_SEND = FulfilmentProviderEnum.NOON_SEND.value
SLIDER = FulfilmentProviderEnum.SLIDER.value
SLIDER_BIKE = FulfilmentProviderEnum.SLIDER_BIKE.value
SLIDER_CAR = FulfilmentProviderEnum.SLIDER_CAR.value
THIRD_PARTY = FulfilmentProviderEnum.THIRD_PARTY.value

#: The bare legacy value and the two tier-pinned ones. All three book through
#: Slider; the tier only decides the vehicle. A move *to* one of them is how a
#: Slider bike is upgraded to a car by hand — and only that direction, because a
#: car zone lists no Slider alternate, so `slider_car -> slider_bike` is never
#: an option `allowed_targets` can offer.
SLIDER_PROVIDERS = frozenset({SLIDER, SLIDER_BIKE, SLIDER_CAR})

#: Where an order may be standing and still be moved.
#:
#: The shape of the list is "the box has not left, or it came back". Everything
#: earlier than `confirmed` has no money behind it and nothing to rescue;
#: `out_for_delivery` means a rider is holding it, which is a fact about the
#: physical world that no amount of pressing buttons changes.
#:
#: `confirmed` and `arrived_at_pos` are here because a batched order sits at
#: `confirmed` for hours with a run already reserved, and a courier that has
#: gone quiet in that window is exactly the case worth catching early — before
#: the kitchen has committed anything to a van that is not coming.
#:
#: `undelivered` is here because it stopped being an ending. A rider carried the
#: box to a door and brought it back; the cake exists, it is paid for, and
#: trying a different courier is a better answer than a refund. What stops the
#: orders that *were* refunded, back when reaching this status paid them out
#: automatically, is the money check in `refuse` — not the status.
MOVABLE_STATUSES = frozenset(
    {
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
        OrderStatusEnum.PACKED,
        OrderStatusEnum.UNDELIVERED,
    }
)


@dataclass(frozen=True)
class Target:
    """One courier this order could be moved to, and whether it actually can."""

    provider: str
    available: bool
    #: Why not, in words for the admin reading them. None when available.
    reason: str | None = None


@dataclass(frozen=True)
class Options:
    """Everything the "change fulfilment" dialog needs to render itself."""

    current: str
    #: What the zone would have chosen. Equal to `current` on the overwhelming
    #: majority of orders, and the difference is the interesting part.
    preferred: str
    targets: tuple[Target, ...]
    #: An order-level refusal, which blocks every target at once. Held apart
    #: from the per-target reasons so the dialog can say "this order cannot be
    #: moved because…" rather than repeating one sentence three times.
    blocked: str | None = None
    #: Whether the thing standing in the way is a live booking rather than
    #: anything about the order itself — the one refusal a person can clear,
    #: by abandoning it. Told apart from the rest so the dialog can offer that
    #: door instead of only closing this one.
    must_abandon_first: bool = False
    #: What abandoning would cost, in rule form. See `Exposure`.
    exposure: Exposure | None = None


@dataclass(frozen=True)
class Exposure:
    """Whether calling off this booking is likely to cost anything.

    **Carries no figure, on purpose.** Neither courier quotes a cancellation fee
    over an API — Lalamove's rule is "free while nobody has been matched" and
    noon Send's is "free before pickup", and that is the whole of what either
    tells us. A number worked out from a published tariff would appear on screen
    next to real quoted costs and read as one of them, and the first time the
    invoice disagreed it would be this dialog that was wrong. State the rule,
    say which side of it this booking is on, and let the invoice be the invoice.
    """

    will_be_charged: bool
    reason: str


async def exposure_of(db: AsyncSession, delivery: OrderDelivery) -> Exposure:
    """What calling this booking off would cost, and whether it happens at all."""
    if not delivery.courier_order_id:
        return Exposure(False, "There is no booking to call off.")
    if is_failed(delivery.provider, delivery.courier_status):
        return Exposure(
            False, "This booking already failed, so there is nothing to cancel."
        )

    run = await batching_service.shared_run_booking(db, delivery)
    if run is not None:
        # The booking is a van carrying other people's cakes, so it is not
        # called off — this order leaves the run and the van keeps its stop.
        # Said plainly because it is a real consequence somebody pays for: a
        # driver will arrive for a box that has gone out with somebody else.
        return Exposure(
            False,
            "This order is on a shared run, so the courier booking stays as it "
            "is — the van will still call for a parcel that has gone with "
            "somebody else. Nobody else's delivery is affected.",
        )
    if delivery.driver_id or delivery.driver_name:
        return Exposure(
            True,
            f"A driver has already been matched, so {delivery.provider} may "
            "charge for calling this off. They do not tell us how much in "
            "advance — it will appear on the invoice.",
        )
    return Exposure(
        False,
        "No driver has been matched yet, so calling this off is free.",
    )


@dataclass(frozen=True)
class Quote:
    """What moving this order to one courier would cost us."""

    provider: str
    #: None for third party, which we do not book and cannot price.
    cost: Decimal | None
    currency: str | None
    distance_m: int | None
    #: Lalamove issues one and will only book against it. noon Send has no
    #: quotation endpoint at all — their price is a rate card, so there is
    #: nothing to pin and nothing to expire.
    quotation_id: str | None
    expires_at: datetime | None
    #: What the customer paid. Restated here because the whole point of the
    #: dialog is the gap between the two.
    fee_charged: Decimal | None
    margin: Decimal | None
    #: The booking this move would call off, if there is one.
    cancels_booking: str | None = None


# ── policy ────────────────────────────────────────────────────────────────────


async def _polygon_for(db: AsyncSession, order: Order, delivery: OrderDelivery):
    """
    The zone whose policy governs this order, or None if we cannot find one.

    Three sources, tried in order, and the first is right for almost everything:

    1. `delivery.polygon_id` — the snapshot taken when the order was placed.
       Preferred because it is the map the order was actually priced and
       promised against, and because it is a stable key rather than a
       recomputation that could land somewhere else.
    2. The pin, resolved against the live map. For orders whose polygon row went
       away with a deleted version — the FK is `SET NULL`, so this is a real
       state rather than a hypothetical one.
    3. Nothing, and the caller falls back to `DEFAULT_ALTERNATES`.
    """
    if delivery.polygon_id is not None:
        polygon = await db.get(DeliveryPolygon, delivery.polygon_id)
        if polygon is not None:
            return polygon

    address = order.shipping_address_snapshot or {}
    lat, lng = address.get("latitude"), address.get("longitude")
    if lat is None or lng is None:
        return None
    return await delivery_zone_service.find_zone(db, float(lat), float(lng))


async def allowed_targets(
    db: AsyncSession, order: Order, delivery: OrderDelivery
) -> tuple[str, str]:
    """
    `(preferred, allowed)` — the zone's courier, and everywhere this order may go.

    The allowed set is the preferred courier **plus** its alternates, minus
    whoever is carrying the order now. Including the preferred one is what lets
    an order come back: a Dubai order moved to a third party when Lalamove went
    quiet is moved back to Lalamove by the same button, with no special case for
    the return leg.
    """
    zone = await _polygon_for(db, order, delivery)
    if zone is not None:
        preferred = zone.fulfilment_provider or THIRD_PARTY
        alternates = tuple(zone.alternate_providers or ())
    else:
        # No zone to ask. The best available guess at what this order was
        # *originally* priced against is what it says on the row — and
        # `original_provider` outranks `provider`, because an order that has
        # already been moved once must not have its own new home mistaken for
        # the map's choice.
        preferred = delivery.original_provider or delivery.provider or THIRD_PARTY
        alternates = tuple(DEFAULT_ALTERNATES.get(preferred, ()))

    allowed = ({preferred} | set(alternates)) - {delivery.provider}
    if delivery.provider == SLIDER_CAR:
        # A Slider car never becomes a bike — the one directional rule. A car
        # zone lists no Slider alternate, so this normally removes nothing; it
        # matters for a *bike* zone that Slider had only a car free for at
        # dispatch, whose `preferred` is still `slider_bike` and would otherwise
        # be offered back.
        allowed -= {SLIDER_BIKE}
    return preferred, tuple(sorted(allowed))


async def refuse(db: AsyncSession, order: Order, delivery: OrderDelivery) -> str | None:
    """
    Why this order may not be moved at all, or None if it may.

    Ordered so the most concrete reason wins. A rider physically holding the box
    is a better sentence than "an order that is out_for_delivery cannot be
    moved", and on an order whose webhook is lagging it is also the only one of
    the two that is true yet.
    """
    # A booked run used to be refused outright here, and that was wrong twice
    # over. It caught runs of a single order, which share a booking with nobody;
    # and even on a real shared run the move is safe — `courier_service.cancel`
    # leaves such a booking alone rather than cancelling the van, so the order
    # simply leaves the run. Refusing turned "the run was dispatched and the
    # driver never came" — the exact situation this feature exists for — into a
    # dead end. What the shop needs there is a warning, and it gets one through
    # `exposure_of`.

    if order.delivery_method != DeliveryMethodEnum.DELIVERY:
        return "A collection order is not carried by anybody, so there is nothing to change."

    if is_collected(delivery.provider, delivery.courier_status):
        return (
            "The driver already has this order. Whoever is carrying it now is "
            "carrying it to the door."
        )

    if delivery.is_driver_on_the_way_here:
        return (
            f"{delivery.driver_name or 'A driver'} is on the way to collect this "
            "order. Abandon the booking first if they are not coming."
        )

    if order.status not in MOVABLE_STATUSES:
        return (
            f"An order that is {order.status.value} cannot be moved to another courier."
        )

    # Last, because it is the least likely and the most surprising, and a reader
    # who gets this far has already been told none of the ordinary reasons
    # apply. Orders that reached `undelivered` while it was still an ending were
    # refunded automatically on the way in; the money is at a bank and no column
    # we write now takes it back.
    if (order.refunded_amount or 0) > 0:
        return (
            f"{order.refunded_amount} has already been refunded on this order, "
            "so it is not ours to deliver."
        )

    return None


async def options_for(
    db: AsyncSession, order: Order, delivery: OrderDelivery
) -> Options:
    """Everything the dialog renders: where this order may go, and where it may not."""
    preferred, allowed = await allowed_targets(db, order, delivery)
    blocked = await refuse(db, order, delivery)

    targets = []
    for provider in allowed:
        reason = blocked
        if reason is None and courier_service.books_itself(provider):
            if not courier_service.is_enabled(provider):
                reason = (
                    f"{provider} is not configured, so nothing can be booked with it."
                )
        targets.append(
            Target(provider=provider, available=reason is None, reason=reason)
        )

    return Options(
        current=delivery.provider,
        preferred=preferred,
        targets=tuple(targets),
        blocked=blocked,
        must_abandon_first=(blocked is not None and delivery.is_driver_on_the_way_here),
        exposure=await exposure_of(db, delivery),
    )


async def _assert_may_move(
    db: AsyncSession, order: Order, delivery: OrderDelivery, target: str
) -> None:
    """Every gate, as exceptions. The single place `quote` and `move` agree."""
    blocked = await refuse(db, order, delivery)
    if blocked is not None:
        raise ConflictError(blocked)

    _, allowed = await allowed_targets(db, order, delivery)
    if target not in allowed:
        if target == delivery.provider:
            raise ConflictError(f"This order is already carried by {target}.")
        raise ConflictError(
            f"This order's zone does not allow moving to {target}. "
            f"It may go to: {', '.join(allowed) or 'nowhere'}."
        )

    if courier_service.books_itself(target) and not courier_service.is_enabled(target):
        raise ServiceUnavailableError(
            f"{target} is not configured, so this order cannot be moved to it."
        )


# ── pricing ───────────────────────────────────────────────────────────────────


async def quote(
    db: AsyncSession, order: Order, *, target: str
) -> tuple[Quote | None, str | None]:
    """
    What moving this order to `target` would cost us, or why we cannot say.

    `(quote, error)` and never raises for a courier problem, matching
    `estimate_for_point` — a courier declining to price a job is an answer, not
    a fault. The gates still raise, because being asked to price a move that is
    not allowed is a programming error rather than a courier one.

    The couriers are priced differently from one another and the shape has to
    admit it rather than pretend otherwise:

    * **Lalamove** issue a quotation with an id, valid for five minutes, and
      will only book against that exact id. So the id travels back to `move`
      and the whole two-step exists to make sure a human agreed to *this*
      number.
    * **noon Send** have no quotation endpoint. Their price is a rate card, so
      it is deterministic, costs nothing to compute and cannot expire — hence no
      id. `may_serve` is asked as well, because their real refusal (an emirate
      boundary, a 20 km cap) happens at task creation and costs a cancellation
      fee by then.
    * **Slider** quote per vehicle tier and issue no id to book against, so
      like noon Send the price is deterministic and cannot expire. `may_serve`
      is asked as well, for the money ceilings — but note their fare endpoint
      is **not** a coverage check (it priced Riyadh and Muscat), so a price here
      is not a promise they will carry it. The 422 at booking is, and `move`
      surfaces it.
    * **third party** is not booked and not priced. There is no number, and
      inventing one would be worse than the honest blank.

    **A human pressing this button is not bound by the automatic routing.**
    Automatic routing hands a Slider zone back to noon Send or Lalamove only when
    Slider is unconfigured; this does not consult that at all, and deliberately —
    the fallback exists so a paid order is never stranded, not to stop somebody
    rescuing a stuck order with the courier standing right there, and whoever
    presses it is looking at the courier's name and its price.
    """
    delivery = await _locked(db, order, lock=False)
    await _assert_may_move(db, order, delivery, target)

    fee = delivery.fee_charged
    cancels = delivery.courier_order_id or None

    def _built(cost, currency, distance_m, quotation_id, expires_at) -> Quote:
        return Quote(
            provider=target,
            cost=cost,
            currency=currency,
            distance_m=distance_m,
            quotation_id=quotation_id,
            expires_at=expires_at,
            fee_charged=fee,
            # The customer's fee is fixed; this is what we would keep of it.
            margin=(None if cost is None or fee is None else Decimal(str(fee)) - cost),
            cancels_booking=cancels,
        )

    if target == THIRD_PARTY:
        return _built(None, None, None, None, None), None

    if target == LALAMOVE:
        found, error = await lalamove_service.quote_for_order(db, order)
        if found is None:
            return None, error
        return (
            _built(
                found.estimate.cost,
                found.estimate.currency,
                found.estimate.distance_m,
                found.estimate.quotation_id,
                found.expires_at,
            ),
            None,
        )

    if target == NOON_SEND:
        in_range, why_not = await noon_send_service.may_serve(db, order)
        if not in_range:
            return None, why_not or "noon Send will not take this order"
        address = order.shipping_address_snapshot or {}
        estimate, error = await noon_send_service.estimate_for_point(
            db,
            float(address.get("latitude")),
            float(address.get("longitude")),
            branch_id=order.branch_id,
        )
        if estimate is None:
            return None, error
        return (
            _built(estimate.cost, estimate.currency, estimate.distance_m, None, None),
            None,
        )

    if target in SLIDER_PROVIDERS:
        allowed, why_not = await slider_service.may_serve(db, order)
        if not allowed:
            return None, why_not or "Slider will not take this order"
        address = order.shipping_address_snapshot or {}
        estimate, error = await slider_service.estimate_for_point(
            db,
            float(address.get("latitude")),
            float(address.get("longitude")),
            branch_id=order.branch_id,
            # The drop's emirate decides the vehicle for a legacy `slider` target;
            # a tier-pinned target (`slider_car` when a bike is upgraded) names it
            # outright. Without one or the other every quote here would price the
            # car, and an order inside Sharjah would be shown a number it will not
            # be charged.
            drop_emirate=str(address.get("city") or ""),
            vehicle=slider_service.vehicle_for_provider(target),
        )
        if estimate is None:
            return None, error
        return (
            _built(estimate.cost, estimate.currency, estimate.distance_m, None, None),
            None,
        )

    return None, f"Unknown courier '{target}'"


# ── the move ──────────────────────────────────────────────────────────────────


async def _locked(
    db: AsyncSession, order: Order, *, lock: bool = True
) -> OrderDelivery:
    """
    This order's delivery row, optionally held against a second admin.

    `FOR UPDATE` is what makes two people pressing "change fulfilment" at the
    same moment safe. The second one waits, re-reads, and finds the provider
    already changed — so it is refused by the ordinary gates rather than booking
    a second courier for a cake that now has one.

    The lock lasts until the transaction commits, and the booking calls commit
    themselves for reasons of their own. That is the correct boundary: once a
    courier has been engaged the interesting race is over.
    """
    statement = select(OrderDelivery).where(OrderDelivery.order_id == order.id)
    if lock:
        statement = statement.with_for_update()
    delivery = (await db.execute(statement)).scalars().first()
    if delivery is None:
        raise ConflictError("This order has no delivery record to move.")
    return delivery


async def _release(db: AsyncSession, order: Order, delivery: OrderDelivery) -> None:
    """
    Let go of whatever is currently arranged, so the new courier starts clean.

    Called before the new booking is made and never after — see `move`. The
    three steps are independent and all three matter:

    * the courier, so nobody drives to a kitchen for a parcel that is going out
      with somebody else;
    * the run, because state that lives in two rows needs the write that updates
      one to update the other, or the batch stays scheduled to collect a stop
      that is not there;
    * the stint, so the ledger does not carry a live driver for a booking that
      no longer exists. `clear` deliberately leaves `driver_assignment_count`
      alone — the register compares it against its own ledger to decide a driver
      slip is owed, so winding it back would make the next driver's slip look
      like one already printed.
    """
    if delivery.courier_order_id:
        previous = list(delivery.previous_courier_order_ids or [])
        booking = delivery.courier_order_id
        provider = delivery.provider

        if is_failed(provider, delivery.courier_status):
            # The courier has already ended it — rejected, expired, cancelled
            # their side. There is nothing to call off, and asking would be
            # asking about somebody else's finished business.
            #
            # This branch is the same question `exposure_of` answers for the
            # dialog, and it has to be, or the two disagree on screen: the
            # dialog said "this booking already failed, so there is nothing to
            # cancel" while the move refused with "the booking could not be
            # called off". Both sentences about one booking, in one panel.
            logger.info(
                "Order %s left a %s booking that had already failed (%s)",
                order.order_number,
                provider,
                delivery.courier_status,
            )
        else:
            # Cleared before the call, so what is read afterwards is *this*
            # call's answer and not something left lying about.
            #
            # That was the bug. `cancel_delivery` fills `last_error` when it
            # fails and clears it when it succeeds — but it returns early
            # without touching the field at all when there is nothing to
            # cancel. So a booking that had already been rejected still carried
            # "Courier rejected the booking — re-dispatch required" from the
            # dispatch that failed hours earlier, and this read it as the
            # cancellation refusing. MM-20260821-001 could not be moved off a
            # courier that had already let it go.
            delivery.last_error = None
            await courier_service.cancel(db, order)

            # A cancel that did not work stops everything, and this is the one
            # place in the module that refuses rather than degrades. Carrying on
            # would leave a live booking on a courier's system for a parcel that
            # has just gone out with somebody else — a rider arriving at the
            # kitchen for a box that is not there, or a second one sent to the
            # customer.
            #
            # The shop is not stuck: the message says to call the courier, and
            # once they have, the booking reads as failed and the branch above
            # takes it.
            if delivery.last_error:
                raise ConflictError(
                    f"The {provider} booking could not be called off — "
                    f"{delivery.last_error}. Cancel it with {provider} directly "
                    "before moving this order, or a driver may still come for it."
                )

        if booking not in previous:
            previous.append(booking)
        delivery.previous_courier_order_ids = previous
        # Their word for it, corrected. `cancel_delivery` writes
        # `order_cancelled`, which is what it is called off for everywhere else
        # and is simply untrue here: the order is very much still happening, it
        # is just happening with somebody else.
        delivery.cancel_reason = "reassigned"
        logger.info(
            "Called off %s booking %s so %s can be moved",
            provider,
            booking,
            order.order_number,
        )

    await batching_service.cancel_assignment(db, delivery)
    await driver_assignment.clear(db, delivery)

    delivery.courier_order_id = None
    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = None
    delivery.share_link = None
    delivery.stop_id = None
    delivery.stop_sequence = None
    delivery.quotation_id = None
    delivery.pod_status = None
    delivery.pod_image_url = None
    # The retry ladder belongs to the attempt that failed, not to the next
    # courier. A move is a person deciding something different; making the new
    # provider inherit two failed rungs would have the sweep give up early on a
    # courier that has not refused anything.
    delivery.dispatch_attempts = 0
    delivery.next_attempt_at = None
    delivery.last_error = None


async def move(
    db: AsyncSession,
    order: Order,
    *,
    target: str,
    quotation_id: str | None = None,
) -> OrderDelivery:
    """
    Hand this order to a different courier.

    **Cancel first, then book.** `order_deliveries` holds one booking per order
    (`uq_order_delivery_order`), so booking the new courier before calling off
    the old one would overwrite `courier_order_id` while the old booking is
    still very much alive on the courier's side — which is how two drivers get
    sent to one cake. Doing it the other way round risks an order with no
    courier at all for a moment, and that is recoverable: it is paid, it is
    boxed, and a person is standing right there looking at the screen.

    It is also cheap. A booking that already has a driver on the way is refused
    by `refuse`, so everything cancelled here is either still looking for a
    driver — free on both couriers — or already dead.

    Commits are not ours. `assign_and_dispatch` and `dispatch_order` both commit
    the moment a rider is engaged, because that cannot be rolled back with our
    transaction; a move to third party writes nothing that needs its own commit
    and rides out on `get_db`.
    """
    delivery = await _locked(db, order)
    # Re-asked under the lock rather than trusting the check the router already
    # did. Between that check and this line another admin may have moved the
    # same order, and the gates are exactly the thing that notices.
    await _assert_may_move(db, order, delivery, target)

    was = delivery.provider
    await _release(db, order, delivery)

    # Set once, and only once. This is "what the map chose for this order", and
    # an order moved twice must not have its first destination mistaken for the
    # zone's answer — `fulfilment_service._estimate` reads it to decide whether
    # this customer was ever promised an hour, and getting that wrong turns
    # "tomorrow before 10 PM" into a time nobody offered.
    if delivery.original_provider is None:
        delivery.original_provider = was
    delivery.provider = target

    if target == THIRD_PARTY:
        # Nothing to book. The box is ready, somebody we already use collects
        # it, and the next thing anybody hears is that it arrived.
        logger.info(
            "Order %s moved from %s to a third party by hand",
            order.order_number,
            was,
        )
    elif target == LALAMOVE:
        found, error = await lalamove_service.quote_for_order(db, order)
        if found is None:
            raise ConflictError(error or "No price available")
        if quotation_id and found.estimate.quotation_id != quotation_id:
            # The number moved between the dialog opening and the button being
            # pressed. Refused with the new one attached rather than booked
            # quietly at a price nobody agreed to.
            raise _price_moved(found, delivery)
        delivery = await lalamove_service.assign_and_dispatch(db, order, quote=found)
    elif target == NOON_SEND:
        result = await noon_send_service.dispatch_order(db, order)
        if result is not None:
            delivery = result
        if not delivery.courier_order_id:
            raise ConflictError(
                delivery.last_error or "noon Send would not take this order"
            )
    elif target in SLIDER_PROVIDERS:
        # `delivery.provider` was set to `target` above, so `dispatch_order` books
        # the tier the move chose — a `slider_car` target books a car. Their only
        # serviceability answer is a 422 at creation (the fare endpoint priced
        # Riyadh and Muscat), so this is where an address outside their area is
        # discovered, and it has to reach the person who pressed the button
        # rather than being logged.
        result = await slider_service.dispatch_order(db, order)
        if result is not None:
            delivery = result
        if not delivery.courier_order_id:
            raise ConflictError(
                delivery.last_error or "Slider would not take this order"
            )
    else:  # pragma: no cover — `_assert_may_move` has already refused this
        raise ConflictError(f"Unknown courier '{target}'")

    if delivery.last_error:
        # The booking failed and the provider has been put back to third party,
        # which is honest: the order is still deliverable by hand. Raised rather
        # than returned as a success carrying an error field, so a client that
        # does not read the field cannot report this as done.
        raise ConflictError(delivery.last_error)

    # The email the move leaves owing, if it leaves one. Never allowed to fail
    # the move — a booking has been made and a mail server is not a reason to
    # unmake it.
    await email_service.repair_after_reassignment(db, order)
    return delivery


def _price_moved(found, delivery: OrderDelivery) -> ConflictError:
    """A 409 that carries the new price, so the dialog can ask again."""
    return ConflictError(
        "The quoted price has expired.",
        payload={
            "message": (
                "The quoted price has expired. Here is the current one — "
                "confirm again to book."
            ),
            "quote": {
                "provider": LALAMOVE,
                "cost": float(found.estimate.cost),
                "currency": found.estimate.currency,
                "distance_m": found.estimate.distance_m,
                "quotation_id": found.estimate.quotation_id,
                "expires_at": (
                    found.expires_at.isoformat() if found.expires_at else None
                ),
                "fee_charged": (
                    float(delivery.fee_charged)
                    if delivery.fee_charged is not None
                    else None
                ),
            },
        },
    )


async def abandon_booking(db: AsyncSession, order: Order) -> OrderDelivery:
    """
    Give up on the courier currently holding this order, without replacing them.

    The escape hatch for the one case `refuse` blocks on purpose: a driver has
    been assigned and has stopped moving. That is not a courier declining a job
    — it is a courier who has accepted one and gone quiet, and it is the most
    common shape of "Lalamove is not responding" there is.

    Deliberately its own action rather than folded into `move`. Cancelling a
    booking with a driver on it can cost a fee on both couriers, and a press
    whose headline is "change provider" should not spend money as a side effect.
    Two deliberate presses, with the figure shown before the first.

    Leaves the order on the same provider with no booking, which is a state
    every dispatch path already understands: `needs_attention` picks it up, the
    admin can re-dispatch, and `move` will now allow a different courier because
    there is no longer a driver on the way.
    """
    delivery = await _locked(db, order)
    if not delivery.courier_order_id:
        raise ConflictError("This order has no courier booking to call off.")
    if is_collected(delivery.provider, delivery.courier_status):
        raise ConflictError(
            "The driver already has this order. Calling off the booking now "
            "would leave nobody accountable for a parcel that has left."
        )

    booking = delivery.courier_order_id
    provider = delivery.provider
    await _release(db, order, delivery)
    delivery.last_error = (
        f"The {provider} booking was called off by hand. This order needs a courier."
    )
    logger.info(
        "Order %s abandoned its %s booking %s", order.order_number, provider, booking
    )
    return delivery
