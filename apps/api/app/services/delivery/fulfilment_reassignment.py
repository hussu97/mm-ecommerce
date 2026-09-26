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
to book, `driver_assignment` to close a stint, `email_service` to make good what
the move leaves owing. A second copy of any of
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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadGatewayError,
    BadRequestError,
    ConflictError,
    ServiceUnavailableError,
)
from app.models.delivery_polygon import (
    DEFAULT_ALTERNATES,
    DeliveryPolygon,
    FulfilmentProviderEnum,
)
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import (
    OrderDelivery,
    is_collected,
    is_failed,
    is_terminal,
)
from app.services import email_service
from app.services.couriers import (
    courier_service,
    lalamove_service,
    noon_send_service,
    slider_service,
)
from app.services.delivery import (
    delivery_zone_service,
    driver_assignment,
)
from app.services.orders import channels, order_lifecycle
from app.services.providers.lalamove_provider import LalamoveError

logger = logging.getLogger(__name__)

__all__ = [
    "FIRST_BOOKING_TARGETS",
    "Exposure",
    "Options",
    "Price",
    "QuotationExpiredError",
    "Quote",
    "Target",
    "abandon_booking",
    "allowed_targets",
    "book_first",
    "exposure_of",
    "move",
    "options_for",
    "price_target",
    "quote",
    "refuse",
]

LALAMOVE = FulfilmentProviderEnum.LALAMOVE.value
NOON_SEND = FulfilmentProviderEnum.NOON_SEND.value
SLIDER_BIKE = FulfilmentProviderEnum.SLIDER_BIKE.value
SLIDER_CAR = FulfilmentProviderEnum.SLIDER_CAR.value
THIRD_PARTY = FulfilmentProviderEnum.THIRD_PARTY.value

#: The two tier-pinned Slider providers. Both book through Slider; the tier only
#: decides the vehicle. A move *to* one of them is how a Slider bike is upgraded
#: to a car by hand — and only that direction, because a car zone lists no Slider
#: alternate, so `slider_car -> slider_bike` is never an option
#: `allowed_targets` can offer. (The legacy bare `slider` was retired in
#: `241_drop_legacy_slider`; its rows are now `slider_car`.)
SLIDER_PROVIDERS = frozenset({SLIDER_BIKE, SLIDER_CAR})

#: Postgres SQLSTATE for a `NOWAIT` lock that could not be taken at once
#: (`lock_not_available`). asyncpg surfaces it on the wrapped DBAPI error's
#: `.sqlstate`; `_locked` turns it into a `ConflictError` (F-COU-14).
_LOCK_NOT_AVAILABLE = "55P03"

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


@dataclass(frozen=True)
class Price:
    """One courier's answer to "what would you charge to carry this?".

    Either a `cost` or a `reason`, never neither: a courier that will not price
    a job is an answer the admin reads, not a fault. The same shape for every
    courier, with the fields that do not apply left empty — Slider and noon Send
    issue no quotation id and so nothing expires.
    """

    provider: str
    cost: Decimal | None = None
    currency: str | None = None
    distance_m: int | None = None
    #: Lalamove only, and the booking must quote it back — see `book_first`.
    quotation_id: str | None = None
    expires_at: datetime | None = None
    #: Why there is no cost, in words for the admin. None when priced.
    reason: str | None = None


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

    if target == THIRD_PARTY:
        cost = None
        price = Price(provider=target)
    else:
        price = await price_target(db, order, target)
        if price.cost is None:
            return None, price.reason
        cost = price.cost

    fee = delivery.fee_charged
    return (
        Quote(
            provider=target,
            cost=cost,
            currency=price.currency,
            distance_m=price.distance_m,
            quotation_id=price.quotation_id,
            expires_at=price.expires_at,
            fee_charged=fee,
            # The customer's fee is fixed; this is what we would keep of it.
            margin=(None if cost is None or fee is None else Decimal(str(fee)) - cost),
            cancels_booking=delivery.courier_order_id or None,
        ),
        None,
    )


async def price_target(db: AsyncSession, order: Order, target: str) -> Price:
    """
    What `target` would charge us to carry this order right now, or why it will not say.

    The per-courier half of `quote`, with no delivery row and no zone policy, so
    a custom order — which has neither until an admin books its first courier —
    can be priced by the same code that prices a move. `quote` still owns the
    gates and the margin; this owns only the courier's answer.

    **Never raises for a courier problem.** A courier that declines to price a
    job, is not configured, or does not exist comes back as a `Price` whose
    `reason` says so and whose `cost` is None — the quotes screen renders it
    beside the courier that did answer, rather than failing the pair.

    Third party is not priced here (there is nobody to ask); `quote` builds that
    blank itself.
    """
    if target not in (LALAMOVE, NOON_SEND) and target not in SLIDER_PROVIDERS:
        return Price(provider=target, reason=f"Unknown courier '{target}'")

    # `quote` has already refused an unconfigured target through
    # `_assert_may_move`; this is for the callers that have no such gate, where
    # Slider's `estimate_for_point` would otherwise answer `(None, None)` — no
    # price and no reason — for a missing API key.
    if not courier_service.is_enabled(target):
        return Price(
            provider=target,
            reason=f"{target} is not configured, so nothing can be booked with it.",
        )

    if target == LALAMOVE:
        found, error = await lalamove_service.quote_for_order(db, order)
        if found is None:
            return Price(provider=target, reason=error or "Lalamove returned no price")
        return Price(
            provider=target,
            cost=found.estimate.cost,
            currency=found.estimate.currency,
            distance_m=found.estimate.distance_m,
            quotation_id=found.estimate.quotation_id,
            expires_at=found.expires_at,
        )

    # Each courier's own gate first — its refusal is the more useful sentence
    # (a money ceiling, an emirate boundary) and it already covers a missing pin.
    if target == NOON_SEND:
        allowed, why_not = await noon_send_service.may_serve(db, order)
        refused = why_not or "noon Send will not take this order"
    else:
        allowed, why_not = await slider_service.may_serve(db, order)
        refused = why_not or "Slider will not take this order"
    if not allowed:
        return Price(provider=target, reason=refused)

    address = order.shipping_address_snapshot or {}
    try:
        latitude = float(address["latitude"])
        longitude = float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        return Price(provider=target, reason="Order has no delivery coordinates")

    if target == NOON_SEND:
        estimate, error = await noon_send_service.estimate_for_point(
            db, latitude, longitude, branch_id=order.branch_id
        )
    else:
        estimate, error = await slider_service.estimate_for_point(
            db,
            latitude,
            longitude,
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
        return Price(provider=target, reason=error or f"{target} returned no price")
    return Price(
        provider=target,
        cost=estimate.cost,
        currency=estimate.currency,
        distance_m=estimate.distance_m,
    )


# ── the move ──────────────────────────────────────────────────────────────────


async def _locked(
    db: AsyncSession, order: Order, *, lock: bool = True
) -> OrderDelivery:
    """
    This order's delivery row, optionally held against a second admin.

    `FOR UPDATE` is what makes two people pressing "change fulfilment" at the
    same moment safe. The second one is refused rather than booking a second
    courier for a cake that now has one.

    **`NOWAIT`, not a plain wait (F-COU-14).** The move this lock guards holds it
    across up to four courier round-trips before it commits — calling off the old
    rider, quoting the new courier, engaging it — which is tens of seconds. A
    plain `FOR UPDATE` would make the second caller (another admin, or the
    dispatch retry sweep) *block* on a request-pool connection for that whole
    stretch, waiting on somebody else's network calls; and there is nothing
    useful for it to do at the end of the wait but turn back, because the first
    move has already changed the very thing the gates check. So it fails fast and
    honestly instead of pinning a connection: a lock it cannot take at once is a
    move already in progress.

    The lock lasts until the transaction commits, and the booking calls commit
    themselves for reasons of their own. That is the correct boundary: once a
    courier has been engaged the interesting race is over.
    """
    statement = select(OrderDelivery).where(OrderDelivery.order_id == order.id)
    if lock:
        delivery = await _first_nowait(db, statement.with_for_update(nowait=True))
    else:
        delivery = (await db.execute(statement)).scalars().first()
    if delivery is None:
        raise ConflictError("This order has no delivery record to move.")
    return delivery


async def _first_nowait(db: AsyncSession, statement):
    """Run a `NOWAIT` locking select; a lock somebody else holds is a 409."""
    try:
        return (await db.execute(statement)).scalars().first()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise ConflictError(
                "Another change to this order's courier is already in "
                "progress. Wait for it to finish, then try again."
            ) from exc
        raise


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

    await _forget_booking(db, delivery)


async def _forget_booking(db: AsyncSession, delivery: OrderDelivery) -> None:
    """
    Wipe the booking off the row so the next courier starts clean.

    The second half of `_release`, and the whole of what `book_first` needs for a
    booking that has already ended on the courier's side: there is nothing to
    call off, only a row to clear. The caller has already kept the old id in
    `previous_courier_order_ids`.
    """
    await driver_assignment.clear(db, delivery)

    delivery.courier_order_id = None
    delivery.courier_previous_status = delivery.courier_status
    delivery.courier_status = None
    delivery.share_link = None
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
    # A person choosing a courier by hand is not the automatic dispatcher falling
    # back, so any "fell back off its zone's courier" note from a past attempt is
    # stale — clear it, the same as the retry ladder above.
    delivery.fallback_reason = None


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


# ── a custom order's first booking ────────────────────────────────────────────

#: The couriers an admin may book for a custom order. A Slider **car** and never
#: a bike — a bespoke cake does not travel in a bike box — and Lalamove. Third
#: party and collection finish the order by hand and never reach this module.
FIRST_BOOKING_TARGETS = frozenset({SLIDER_CAR, LALAMOVE})

#: Where a custom order may stand to be booked. `packed` because the cake is
#: made and boxed (and packing is what consumed its recipe, so nothing leaves
#: the shop unconsumed); `undelivered` because a rider brought it back and the
#: admin is choosing again.
_FIRST_BOOKABLE_STATUSES = frozenset(
    {OrderStatusEnum.PACKED, OrderStatusEnum.UNDELIVERED}
)

#: How far the pin may sit from the quotation's drop-off before the price is
#: treated as belonging to a different address — ~11 m, the same rounding the
#: checkout quote cache uses. The contact and address stay editable until a
#: courier is booked, so a pin can move between the quote and the button.
_PIN_TOLERANCE_DEGREES = 1e-4


class QuotationExpiredError(ConflictError):
    """409: the Lalamove price the admin approved can no longer be booked.

    Coded so the console re-fetches the quotes instead of only showing the
    message — the one conflict here a client should act on rather than report.
    """

    def __init__(
        self,
        detail: str = (
            "The Lalamove price you chose has expired. Refresh the delivery "
            "quotes and choose again."
        ),
    ):
        super().__init__(detail)
        self.code = "delivery_quote_expired"


async def book_first(
    db: AsyncSession,
    order: Order,
    target: str,
    *,
    quotation_id: str | None = None,
) -> OrderDelivery:
    """
    Book the courier an admin chose for a custom order.

    `move` cannot do this: it needs a delivery row to move *from*, asks the
    zone where the order may go, and mails the customer afterwards. A custom
    order has no row until this call, no zone that chose anything, and its
    customer hears only courier news (`channels`). And `courier_service.dispatch`
    must not do it either: when Slider refuses it quietly books noon Send or
    Lalamove instead — money spent on a courier nobody chose — and its Lalamove
    path re-quotes, so the price booked is not the price shown.

    So this books exactly `target`, or raises and books nothing:

    * **No fallback.** Slider refusing is a `ConflictError` carrying Slider's
      reason; the admin chooses again.
    * **No retry ladder.** A failure leaves `next_attempt_at` empty, so
      `retry_failed_dispatches` — which books through `dispatch`, fallback and
      all — never picks the order up behind the admin's back.
    * **Lalamove only at the approved price.** `quotation_id` is the one the
      quotes screen showed; the booking is placed against that quotation, and a
      lapsed one is `QuotationExpiredError` for the console to re-quote.
    * **No zone policy and no email.**

    On failure the request rolls back whatever was written here (the new row,
    the `undelivered → packed` step). On success the courier arm has already
    committed — see the comment at the call.
    """
    if not channels.is_custom(order.source):
        raise BadRequestError(
            "Only a custom order is booked this way. Use Change courier on any "
            "other order."
        )
    if target not in FIRST_BOOKING_TARGETS:
        raise BadRequestError(
            f"A custom order can go by {' or '.join(sorted(FIRST_BOOKING_TARGETS))}"
            f", not '{target}'."
        )
    if target == LALAMOVE and not quotation_id:
        raise BadRequestError(
            "Lalamove is booked only at a price you have seen. Fetch the delivery "
            "quotes and choose again."
        )
    if not courier_service.is_enabled(target):
        raise ServiceUnavailableError(
            f"{target} is not configured, so nothing can be booked with it."
        )

    # Held against a second admin pressing the same button, and against the
    # order moving under us (a cancel, a hand finish). The order row rather than
    # the delivery row because on a first booking there is no delivery row to
    # lock. `NOWAIT` for the reason `_locked` gives: the hold spans courier
    # round-trips. `FOR NO KEY UPDATE` so rows that merely reference the order —
    # status events, the delivery row we are about to insert — are not blocked.
    # `populate_existing` re-reads the status and contact under the lock; the
    # flush first so nothing the caller set in memory is overwritten by it.
    await db.flush()
    await _first_nowait(
        db,
        select(Order)
        .where(Order.id == order.id)
        .with_for_update(nowait=True, key_share=True)
        .execution_options(populate_existing=True),
    )

    if order.status not in _FIRST_BOOKABLE_STATUSES:
        raise ConflictError(
            "A custom order is booked once it is packed, or again after it came "
            f"back undelivered. This one is {order.status.value}."
        )
    missing = _missing_for_courier(order)
    if missing:
        raise BadRequestError("A courier needs " + ", ".join(missing) + ".")

    existing = (
        (
            await db.execute(
                select(OrderDelivery)
                .where(OrderDelivery.order_id == order.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .first()
    )
    if (
        existing is not None
        and existing.courier_order_id
        and not is_terminal(existing.provider, existing.courier_status)
    ):
        # Not ours to call off from here: this path books, it does not replace.
        # A live booking is cancelled deliberately (the custom order's finish or
        # cancel) and then this runs on a row whose booking has ended.
        raise ConflictError(
            f"This order already has a live {existing.provider} booking "
            f"({existing.courier_order_id}). Call it off before booking another."
        )

    # Every question that can be answered without writing anything, before
    # anything is written.
    approved = None
    if target == LALAMOVE:
        approved = await _approved_lalamove_quote(order, quotation_id or "")
    else:
        allowed, why_not = await slider_service.may_serve(db, order)
        if not allowed:
            raise ConflictError(why_not or "Slider will not take this order.")

    delivery = await _row_for_first_booking(db, order, target, existing)

    # A rider brought it back; the box is on the shelf again. The map sends a
    # failed hand-over back through `packed` (the website re-dispatch lands in
    # the same place, via `stamp_packed` after its booking). Done *before*
    # booking so the courier arm's commit carries the status and the booking
    # together: stamped afterwards, a failure between the two commits would
    # leave a live booking on an `undelivered` order, and the courier's pickup
    # push (`undelivered → out_for_delivery` is not a transition) would be
    # skipped. Re-packing consumes nothing twice — `accept_order` is idempotent
    # per inventory revision.
    if order.status == OrderStatusEnum.UNDELIVERED:
        await order_lifecycle.transition(db, order, OrderStatusEnum.PACKED)

    if approved is not None:
        try:
            # COMMITS the session on success: a driver has been engaged and the
            # Lalamove wallet debited outside our transaction, so the booking
            # (with the row and the status above) is written now rather than
            # left to the request, which could still fail and roll back a
            # booking that exists. See `lalamove_service.assign_and_dispatch`.
            delivery = await lalamove_service.assign_and_dispatch(
                db, order, quote=approved
            )
        except lalamove_service.QuotationExpired as exc:
            raise QuotationExpiredError() from exc
    else:
        # COMMITS the session on success, for the same reason: a Slider rider
        # has been engaged and cannot be rolled back with the request. See
        # `slider_service.dispatch_order`. The row names `slider_car`, so the
        # car is what is asked for.
        result = await slider_service.dispatch_order(db, order)
        if result is not None:
            delivery = result

    if not delivery.courier_order_id or delivery.last_error:
        # Nothing was committed: both arms commit only once a rider exists.
        # Raised so the request rolls the row and the status step back, and so
        # a client that does not read fields cannot report this as done.
        raise ConflictError(
            delivery.last_error or f"{target} would not take this order."
        )

    logger.info(
        "Custom order %s booked on %s as %s (%s %s)",
        order.order_number,
        delivery.provider,
        delivery.courier_order_id,
        delivery.quoted_currency or "",
        delivery.cost_total if delivery.cost_total is not None else "-",
    )
    return delivery


def _missing_for_courier(order: Order) -> list[str]:
    """What a courier booking needs that this order does not have.

    The customer's name and phone are also the recipient's: the custom service
    mirrors them into the address snapshot, which is what both courier arms
    hand the rider (`address_format.delivery_contact`).
    """
    missing = []
    address = order.shipping_address_snapshot or {}
    try:
        float(address["latitude"])
        float(address["longitude"])
    except (KeyError, TypeError, ValueError):
        missing.append("a location pin")
    if not (order.customer_name or "").strip():
        missing.append("the customer's name")
    if not (order.customer_phone or "").strip():
        missing.append("the customer's phone")
    return missing


async def _row_for_first_booking(
    db: AsyncSession, order: Order, target: str, delivery: OrderDelivery | None
) -> OrderDelivery:
    """The delivery row this booking will write to, created or made ready.

    Created the checkout way (`record_order_delivery`) on a first booking, with
    no zone — a custom order was priced by the shop, not by the map. After an
    `undelivered`, the existing row, with the ended booking kept in
    `previous_courier_order_ids` and cleared the way `move` clears one. The
    caller has already refused a row whose booking is still live.
    """
    if delivery is None:
        return await lalamove_service.record_order_delivery(
            db, order, zone=None, cart=None, provider=target
        )

    if delivery.courier_order_id:
        # Ended on the courier's side — returned, rejected, expired, or
        # completed and then marked undelivered here. Nothing to cancel; kept
        # so the history still names it.
        previous = list(delivery.previous_courier_order_ids or [])
        if delivery.courier_order_id not in previous:
            previous.append(delivery.courier_order_id)
        delivery.previous_courier_order_ids = previous
        await _forget_booking(db, delivery)

    # Set once, the way `move` sets it: a second courier after an undelivered is
    # a person choosing differently, and the first choice is what the "moved
    # from" badge names.
    if delivery.provider != target and delivery.original_provider is None:
        delivery.original_provider = delivery.provider
    delivery.provider = target
    # The retry ladder and any fallback note belong to earlier attempts. Cleared
    # even without a booking to forget, so no sweep inherits them.
    delivery.dispatch_attempts = 0
    delivery.next_attempt_at = None
    delivery.last_error = None
    delivery.fallback_reason = None
    return delivery


async def _approved_lalamove_quote(
    order: Order, quotation_id: str
) -> lalamove_service.ReassignQuote:
    """
    The quotation the admin approved, read back from Lalamove — not a new one.

    `quote_for_order` would issue a fresh quotation, at a price nobody saw and
    under a new id. Lalamove keep a quotation readable by id for its five
    minutes, stops and all, and `place_order` must quote the stop ids it was
    priced for, so reading it back is how the booking spends the number on the
    screen. A read, not a booking, so no money moves here.

    Refused with `QuotationExpiredError` when it has lapsed, and also when the
    pin has moved since it was quoted: that price was for somewhere else.
    """
    try:
        quotation = await lalamove_service.provider.get_quotation(quotation_id)
    except LalamoveError as exc:
        if exc.is_expired_quotation or exc.status == 404:
            raise QuotationExpiredError() from exc
        raise BadGatewayError(
            f"Lalamove could not read that price back: {exc}"
        ) from exc

    estimate = lalamove_service.parse_quotation(quotation)
    stops = quotation.get("stops") or []
    if estimate is None or len(stops) < 2:
        raise QuotationExpiredError()
    expires_at = lalamove_service.parse_time(quotation.get("expiresAt"))
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise QuotationExpiredError()

    drop = stops[-1].get("coordinates") or {}
    address = order.shipping_address_snapshot or {}
    try:
        moved = (
            abs(float(drop["lat"]) - float(address["latitude"]))
            > _PIN_TOLERANCE_DEGREES
            or abs(float(drop["lng"]) - float(address["longitude"]))
            > _PIN_TOLERANCE_DEGREES
        )
    except (KeyError, TypeError, ValueError):
        # A quotation that does not echo its coordinates cannot be checked; the
        # id alone is what Lalamove will book against.
        moved = False
    if moved:
        raise QuotationExpiredError(
            "The delivery pin has moved since this Lalamove price was quoted. "
            "Refresh the delivery quotes and choose again."
        )

    return lalamove_service.ReassignQuote(
        # The id asked for, whatever the read echoes: it is the one the admin
        # agreed to and the one `place_order` will be told.
        estimate=replace(estimate, quotation_id=quotation_id),
        stops=stops,
        expires_at=expires_at,
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
