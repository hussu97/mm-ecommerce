from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.cart import Cart
from app.models.delivery_batch import DELIVERY_TIMEZONE
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import (
    batching_service,
    courier_service,
    delivery_zone_service,
    lalamove_service,
    trial_customer,
)
from app.services.delivery_zone_service import Zone

__all__ = [
    "DeliveryEstimate",
    "DeliveryPrice",
    "UnserviceableAreaError",
    "calculate_fee",
    "estimate_arrival",
    "price",
    "quote",
    "get_delivery_rates",
    "get_settings",
    "round_up_aed",
]

TZ = ZoneInfo(DELIVERY_TIMEZONE)

#: How long after a run leaves the kitchen the last box on it is through a door.
#: One number for every drop on the route rather than a per-stop calculation:
#: the route is optimised by the courier after we hand it over, so which stop is
#: last is not knowable when the promise is made. An hour covers the city zones,
#: which are the only ones this applies to.
DISPATCH_TO_DOOR = timedelta(hours=1)

#: What the customer is told when nothing can be quoted to their pin. Says
#: nothing about who would have carried it — that a courier exists at all is not
#: the shopper's business, and a message naming one would be the first place the
#: fulfilment map leaked onto the storefront.
UNSERVICEABLE_MESSAGE = (
    "We can't deliver to this location right now. "
    "Please choose a different address, or collect from the store instead."
)


class UnserviceableAreaError(BadRequestError):
    """No price exists for this pin, so no order can be written against it.

    Raised only for a dynamically-priced area whose fee *is* the courier quote.
    A zone with a fixed fee is priced without asking anyone, so a courier being
    unreachable there is a dispatch problem for an admin — never a checkout
    failure for a customer.

    A `BadRequestError` rather than something new, so it reaches the shopper as
    a 400 carrying its own message instead of a 500 that tells them nothing.
    """

    def __init__(self, detail: str = UNSERVICEABLE_MESSAGE) -> None:
        super().__init__(detail)


def round_up_aed(amount: Decimal) -> Decimal:
    """
    Up to the whole dirham.

    A courier quote arrives as 26.40 or 31.05, and a delivery line with fils in
    it reads like a mistake next to prices that never have any. Always up, never
    to nearest: rounding down would have us absorb the difference on every
    single order, and the amounts are small enough that the honest direction is
    the one that cannot quietly cost money.
    """
    return amount.to_integral_value(rounding=ROUND_CEILING).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class DeliveryEstimate:
    """When the customer should expect the box, and how precisely we know it."""

    #: On the shop's clock, which is the clock the customer is standing on.
    at: datetime
    #: `"time"` when we can name an hour, `"day"` when only the date is ours to
    #: promise. The distinction is the whole value of this object: rendering a
    #: third-party delivery as "tomorrow, 14:00" would invent a precision that
    #: belongs to somebody else's schedule.
    precision: str


async def estimate_arrival(
    db: AsyncSession, zone: Zone | None, *, now: datetime | None = None
) -> DeliveryEstimate:
    """
    When delivery to this zone should land.

    Two very different kinds of answer, because there are two very different
    kinds of knowledge behind them.

    In a zone we dispatch ourselves the schedule is ours: the order joins the
    run whose window is open now, that run leaves when the window closes, and an
    hour later it is delivered. Every term in that is a number we control, so it
    is safe to name an hour. A zone we dispatch but never batch — noon Send —
    skips the waiting entirely and is simply an hour from now.

    A third-party zone is collected on a schedule we cannot see. The only thing
    we can honestly commit to is the next day, and it is the next day whether
    the order came in at nine in the morning or five past eleven at night —
    saying "today" for an early order would be guessing with someone else's van.
    """
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(TZ)

    if zone is None or not zone.books_itself:
        return DeliveryEstimate(
            at=datetime.combine(
                local.date() + timedelta(days=1), local.time().min, tzinfo=TZ
            ),
            precision="day",
        )

    if not zone.is_batched:
        # Ours to dispatch, but never batched: a noon Send order is handed to a
        # rider as soon as it is packed. There is no window to wait for, so the
        # hour starts now.
        return DeliveryEstimate(at=local + DISPATCH_TO_DOOR, precision="time")

    windows = await batching_service.active_windows(db, zone.id)
    match = batching_service.find_window(windows, now)
    if match is None:
        # No schedule, or a hole in one. Dispatch does not wait in that case —
        # the order goes out on its own immediately — so neither does this.
        return DeliveryEstimate(at=local + DISPATCH_TO_DOOR, precision="time")
    return DeliveryEstimate(at=match.dispatch_at + DISPATCH_TO_DOOR, precision="time")


@dataclass(frozen=True)
class DeliveryPrice:
    """
    What delivery costs to one point, and everything that decided it.

    One object rather than a bare number because three callers need different
    parts of the same answer: the checkout wants the figure *and* whether the
    address is deliverable at all, order creation wants the fee and the zone
    that carries it, and the margin record wants the courier's own estimate.
    Deriving those separately is how the price on screen and the price on the
    order end up disagreeing.
    """

    zone: Zone | None
    #: What delivery costs before free delivery is applied. `None` means we
    #: cannot say — either there is no pin yet, or the courier would not quote a
    #: dynamic area, which `serviceable` distinguishes.
    base_fee: Decimal | None
    free_applied: bool
    #: Whether free delivery can apply to this pin at all, regardless of what is
    #: in the basket. False in the zones we do not price ourselves — there is no
    #: fixed fee there to waive, only a courier bill that arrives whatever the
    #: order is worth. Kept separate from `free_applied` so the storefront can
    #: stop dangling an offer that will never land here.
    free_available: bool
    #: False only when a dynamic area could not be quoted. Everything else,
    #: including "no pin yet", is serviceable until proven otherwise.
    serviceable: bool
    is_dynamic: bool
    estimate: lalamove_service.Estimate | None = None
    error: str | None = None

    @property
    def fee(self) -> Decimal | None:
        """
        What the customer actually pays, free delivery included.

        `None` only when the amount is genuinely unknown — no pin, or nowhere we
        can deliver to.
        """
        if not self.serviceable:
            return None
        if self.free_applied:
            return Decimal("0.00")
        return self.base_fee


async def get_settings(db: AsyncSession) -> DeliverySettings:
    result = await db.execute(select(DeliverySettings))
    settings = result.scalars().first()
    if settings is None:
        # Fallback if table is empty (should not happen after migration)
        settings = DeliverySettings(
            free_delivery_threshold=Decimal("150.00"),
            pickup_fee=Decimal("0.00"),
            default_delivery_fee=Decimal("50.00"),
        )
    if settings.default_delivery_fee is None:
        # A row written before the column existed, or a fixture that predates
        # it. Match the old "Rest of UAE" price rather than charging nothing.
        settings.default_delivery_fee = Decimal("50.00")
    return settings


async def price(
    db: AsyncSession,
    subtotal: Decimal,
    *,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    address: str | None = None,
    settings: DeliverySettings | None = None,
) -> DeliveryPrice:
    """
    Price delivery to one pin. The single place that decision is made.

    The pin decides it, and only the pin. There used to be a self-declared
    emirate to fall back on; it was always a guess — wrong for an address a few
    hundred metres over a boundary, and wrong for everyone who left the dropdown
    on its default.

    Where the number comes from depends on the zone the pin lands in. Around the
    kitchen the fee is published and fixed. Everywhere else it is the courier's
    own quote for that exact trip, rounded up, which is the only figure that
    stays honest across an emirate we cannot draw a flat price around. A pin
    nobody will quote is not a cheap delivery — it is one we cannot make, and
    saying so is better than taking the money and finding out later.

    Free delivery is a flag on the zone and nothing else. It used to be read off
    the pricing mode, which held only while "fixed fee" and "we deliver it
    ourselves" described the same three zones. They no longer do — the outer
    zones are fixed-fee and third-party — and a proxy that has stopped being
    true gives delivery away in exactly the places that cost most to reach.
    """
    if settings is None:
        settings = await get_settings(db)
    qualifies = subtotal >= settings.free_delivery_threshold

    if latitude is None or longitude is None:
        # Nothing to price yet, and nothing to promise: whether free delivery
        # reaches this order is a property of an address we have not been given.
        # `free_available` stays true so the basket can still be encouraged
        # towards the threshold — the copy that does it says "in selected
        # areas", which is exactly the uncertainty this state is in.
        return DeliveryPrice(
            zone=None,
            base_fee=None,
            free_applied=False,
            free_available=True,
            serviceable=True,
            is_dynamic=False,
        )

    zone = await delivery_zone_service.find_zone(db, float(latitude), float(longitude))
    # No zone at all is treated as dynamic rather than as the default fee: an
    # address outside every drawn shape is exactly the case we know least about,
    # and asking the courier is a better answer than a flat guess.
    is_dynamic = zone is None or zone.is_dynamic

    # Asked for on every quote, including in the fixed-fee zones, because the
    # gap between what we charge and what a run costs is the number the fee
    # table gets argued with later.
    #
    # Routed by the zone's own courier and its own kitchen. A noon Send zone
    # costed on Lalamove's rate card would look like it loses money on every
    # order when it makes a few dirhams, and a second kitchen would be costed
    # from the first one's front door.
    estimate, error = await courier_service.estimate_for_point(
        db,
        zone.fulfilment_provider if zone else None,
        float(latitude),
        float(longitude),
        address,
        zone.branch_id if zone else None,
    )

    eligible = zone is not None and zone.free_delivery_eligible

    if not is_dynamic:
        return DeliveryPrice(
            zone=zone,
            base_fee=zone.delivery_fee,
            free_applied=qualifies and eligible,
            free_available=eligible,
            serviceable=True,
            is_dynamic=False,
            estimate=estimate,
            error=error,
        )

    if estimate is not None:
        return DeliveryPrice(
            zone=zone,
            base_fee=round_up_aed(estimate.cost),
            free_applied=qualifies and eligible,
            free_available=eligible,
            serviceable=True,
            is_dynamic=True,
            estimate=estimate,
            error=error,
        )

    if not lalamove_service.is_enabled():
        # There is nobody to ask, which is a configuration state rather than a
        # statement about this address. Refusing every dynamic order because a
        # credential is missing would take the whole country offline; the
        # configured default fee keeps the shop selling.
        return DeliveryPrice(
            zone=zone,
            base_fee=settings.default_delivery_fee,
            free_applied=False,
            free_available=False,
            serviceable=True,
            is_dynamic=True,
            estimate=None,
            error=error,
        )

    return DeliveryPrice(
        zone=zone,
        base_fee=None,
        free_applied=False,
        free_available=False,
        serviceable=False,
        is_dynamic=True,
        estimate=None,
        error=error,
    )


async def calculate_fee(
    delivery_method: DeliveryMethodEnum,
    subtotal: Decimal,
    db: AsyncSession,
    settings: DeliverySettings | None = None,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    address: str | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> Decimal:
    """
    The delivery fee in AED, or `UnserviceableAreaError` if there is no such fee.

    An order without coordinates cannot be delivered anyway, so the default fee
    is the honest answer for one.

    The trial accounts pay nothing, anywhere. Their orders exist to exercise the
    courier pipeline on production, and a test that costs the tester money is a
    test that gets run less often than it should. The identity is passed in
    rather than looked up here so that the two callers that know it — the
    checkout quote and the order being written — are the only two that can grant
    it, and so both grant it identically.
    """
    if settings is None:
        settings = await get_settings(db)

    if delivery_method == DeliveryMethodEnum.PICKUP:
        return settings.pickup_fee

    priced = await price(
        db,
        subtotal,
        latitude=latitude,
        longitude=longitude,
        address=address,
        settings=settings,
    )
    # After `price`, not before: a trial account still may not order somewhere we
    # cannot deliver to. Free is a discount, not an exemption from geography.
    if not priced.serviceable:
        raise UnserviceableAreaError()
    if trial_customer.is_trial_customer(user_id, email):
        return Decimal("0.00")
    fee = priced.fee
    return settings.default_delivery_fee if fee is None else fee


async def quote(
    db: AsyncSession,
    subtotal: Decimal,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    cart: Cart | None = None,
    address: str | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> dict:
    """
    What delivery would cost to this point, for the checkout to show live.

    When a basket is supplied the courier's own estimate is written onto it. In
    a fixed-fee zone that number is never shown — it exists so the zone's price
    can be argued with later. In a dynamic zone it *is* the price, rounded up.
    Either way the response is deliberately silent about where it came from:
    nothing that reaches a browser should hint at who delivers.

    `free_delivery_available` is the one thing here that is about the offer
    rather than the price. It says whether free delivery can reach this pin at
    all, so the storefront can stop advertising it where it cannot.

    `delivery_estimate` answers the question every shopper actually has, which
    is not "how much" but "when". It is only present once there is a pin,
    because before then there is no schedule to read it off.

    A trial account sees free delivery, and sees it the same way anyone over the
    threshold does. Presenting it as its own kind of discount would mean a
    second state for the checkout to render, and the account exists to walk the
    ordinary path rather than a special one.
    """
    settings = await get_settings(db)
    priced = await price(
        db,
        subtotal,
        latitude=latitude,
        longitude=longitude,
        address=address,
        settings=settings,
    )

    # A trial account pays nothing anywhere it can be delivered to. Applied here
    # rather than inside `price` so the waiver stays a property of who is
    # ordering rather than of the map — and applied at the same point
    # `calculate_fee` applies it, which is what keeps the price on the checkout
    # and the price on the order the same number.
    waived = trial_customer.is_trial_customer(user_id, email) and priced.serviceable
    fee = Decimal("0.00") if waived else priced.fee
    free_applied = priced.free_applied or waived

    if (
        cart is not None
        and latitude is not None
        and longitude is not None
        and (priced.estimate is not None or priced.error is not None)
    ):
        await lalamove_service.record_cart_estimate(
            db,
            cart,
            zone=priced.zone,
            # The fee actually charged, free delivery included, because the
            # comparison that matters is cost against revenue. An unserviceable
            # pin charges nothing because it sells nothing.
            fee=fee if fee is not None else Decimal("0.00"),
            latitude=float(latitude),
            longitude=float(longitude),
            estimate=priced.estimate,
            error=priced.error,
        )

    estimate = (
        await estimate_arrival(db, priced.zone)
        if latitude is not None and longitude is not None and priced.serviceable
        else None
    )

    return {
        "delivery_fee": float(fee) if fee is not None else None,
        # What it would have cost, so the summary can strike it through and
        # show the customer the saving they just earned.
        "base_fee": float(priced.base_fee) if priced.base_fee is not None else None,
        "free_delivery_applied": free_applied,
        # Whether the offer reaches this address at all. Without it the checkout
        # would keep telling someone in Abu Dhabi they are 40 dirhams from free
        # delivery, which is an offer that does not exist there.
        "free_delivery_available": priced.free_available,
        "free_threshold": float(settings.free_delivery_threshold),
        # Zero once it is free, however it became free. Otherwise a trial
        # account would be shown "free delivery" and "AED 150 to go" at once.
        "remaining_for_free": 0.0
        if free_applied
        else float(max(Decimal("0.00"), settings.free_delivery_threshold - subtotal)),
        "zone_name": priced.zone.name if priced.zone else None,
        "in_known_zone": priced.zone is not None,
        "serviceable": priced.serviceable,
        # Sent as an instant plus how precisely we mean it, and formatted by the
        # storefront: "tomorrow" and the time format are language, and language
        # is the browser's job, not this endpoint's.
        "delivery_estimate": (
            {"at": estimate.at.isoformat(), "precision": estimate.precision}
            if estimate is not None
            else None
        ),
    }


async def get_delivery_rates(db: AsyncSession) -> dict:
    """
    The delivery numbers a storefront can render before it has a pin.

    Deliberately not a list of areas and prices. The fee comes from where the
    pin lands, and publishing a price table would invite the storefront to
    guess from an address string — which is exactly the guess the polygon map
    replaced.
    """
    settings = await get_settings(db)
    return {
        "free_threshold": float(settings.free_delivery_threshold),
        "pickup_fee": float(settings.pickup_fee),
        "default_delivery_fee": float(settings.default_delivery_fee),
    }
