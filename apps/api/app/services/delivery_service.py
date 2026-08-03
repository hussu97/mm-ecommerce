from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError
from app.models.cart import Cart
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import delivery_zone_service, lalamove_service
from app.services.delivery_zone_service import Zone

__all__ = [
    "DeliveryPrice",
    "UnserviceableAreaError",
    "calculate_fee",
    "price",
    "quote",
    "get_delivery_rates",
    "get_settings",
    "round_up_aed",
]

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

        `None` only when the amount is genuinely unknown. A basket over the
        threshold is zero even before there is a pin — what delivery *would*
        have cost is still unknown, but what it costs is not.
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
            free_delivery_threshold=Decimal("200.00"),
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
    """
    if settings is None:
        settings = await get_settings(db)
    free_applied = subtotal >= settings.free_delivery_threshold

    if latitude is None or longitude is None:
        # Nothing to price yet. Not an error and not a fee — the checkout says
        # "once you add your address" rather than showing a number it will have
        # to take back.
        return DeliveryPrice(
            zone=None,
            base_fee=None,
            free_applied=free_applied,
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
    estimate, error = await lalamove_service.estimate_for_point(
        db, float(latitude), float(longitude), address
    )

    if not is_dynamic:
        return DeliveryPrice(
            zone=zone,
            base_fee=zone.delivery_fee,
            free_applied=free_applied,
            serviceable=True,
            is_dynamic=False,
            estimate=estimate,
            error=error,
        )

    if estimate is not None:
        return DeliveryPrice(
            zone=zone,
            base_fee=round_up_aed(estimate.cost),
            free_applied=free_applied,
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
            free_applied=free_applied,
            serviceable=True,
            is_dynamic=True,
            estimate=None,
            error=error,
        )

    return DeliveryPrice(
        zone=zone,
        base_fee=None,
        free_applied=free_applied,
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
) -> Decimal:
    """
    The delivery fee in AED, or `UnserviceableAreaError` if there is no such fee.

    An order without coordinates cannot be delivered anyway, so the default fee
    is the honest answer for one.
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
    if not priced.serviceable:
        raise UnserviceableAreaError()
    fee = priced.fee
    return settings.default_delivery_fee if fee is None else fee


async def quote(
    db: AsyncSession,
    subtotal: Decimal,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    cart: Cart | None = None,
    address: str | None = None,
) -> dict:
    """
    What delivery would cost to this point, for the checkout to show live.

    When a basket is supplied the courier's own estimate is written onto it. In
    a fixed-fee zone that number is never shown — it exists so the zone's price
    can be argued with later. In a dynamic zone it *is* the price, rounded up.
    Either way the response is deliberately silent about where it came from:
    nothing that reaches a browser should hint at who delivers.
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
            fee=priced.fee if priced.fee is not None else Decimal("0.00"),
            latitude=float(latitude),
            longitude=float(longitude),
            estimate=priced.estimate,
            error=priced.error,
        )

    return {
        "delivery_fee": float(priced.fee) if priced.fee is not None else None,
        # What it would have cost, so the summary can strike it through and
        # show the customer the saving they just earned.
        "base_fee": float(priced.base_fee) if priced.base_fee is not None else None,
        "free_delivery_applied": priced.free_applied,
        "free_threshold": float(settings.free_delivery_threshold),
        "remaining_for_free": float(
            max(Decimal("0.00"), settings.free_delivery_threshold - subtotal)
        ),
        "zone_name": priced.zone.name if priced.zone else None,
        "in_known_zone": priced.zone is not None,
        "serviceable": priced.serviceable,
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
