from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cart import Cart
from app.models.delivery_settings import DeliverySettings
from app.models.order import DeliveryMethodEnum
from app.services import (
    courier_service,
    delivery_zone_service,
    lalamove_service,
    trial_customer,
)

__all__ = [
    "calculate_fee",
    "quote",
    "get_delivery_rates",
    "get_settings",
]


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


async def calculate_fee(
    delivery_method: DeliveryMethodEnum,
    subtotal: Decimal,
    db: AsyncSession,
    settings: DeliverySettings | None = None,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> Decimal:
    """
    The delivery fee in AED.

    The pin decides it, and only the pin. There used to be a self-declared
    emirate to fall back on; it was always a guess — wrong for an address a few
    hundred metres over a boundary, and wrong for everyone who left the
    dropdown on its default — and an order without coordinates cannot be
    delivered anyway, so the default fee is the honest answer for one.

    The single exception is the trial accounts, who pay nothing: their orders
    exist to exercise the courier pipeline on production, and a test that costs
    the tester money is a test that gets run less often than it should. The
    identity is passed in rather than looked up here so that the two callers
    that know it — the checkout quote and the order being written — are the only
    two that can grant it.
    """
    if settings is None:
        settings = await get_settings(db)

    if delivery_method == DeliveryMethodEnum.PICKUP:
        return settings.pickup_fee

    if trial_customer.is_trial_customer(user_id, email):
        return Decimal("0.00")

    if subtotal >= settings.free_delivery_threshold:
        return Decimal("0.00")

    if latitude is not None and longitude is not None:
        zone = await delivery_zone_service.find_zone(
            db, float(latitude), float(longitude)
        )
        if zone is not None:
            return zone.delivery_fee
        # A real address we have simply not drawn a zone around yet.
        return settings.default_delivery_fee

    return settings.default_delivery_fee


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

    When a basket is supplied this also asks the courier what the same trip
    would cost *us*, and writes that onto the basket. The customer is never
    told: the fee they see is the zone's, and the courier's number exists so
    the zone's number can be argued with later. It is deliberately absent from
    the response — nothing that reaches a browser should hint at who delivers.

    A trial account sees free delivery, and sees it the same way anyone over the
    threshold does. Presenting it as its own kind of discount would mean a
    second state for the checkout to render, and the account exists to walk the
    ordinary path rather than a special one.
    """
    settings = await get_settings(db)
    zone = (
        await delivery_zone_service.find_zone(db, float(latitude), float(longitude))
        if latitude is not None and longitude is not None
        else None
    )
    base_fee = zone.delivery_fee if zone else settings.default_delivery_fee
    qualifies = subtotal >= settings.free_delivery_threshold or (
        trial_customer.is_trial_customer(user_id, email)
    )

    if cart is not None and latitude is not None and longitude is not None:
        estimate, error = await courier_service.estimate_for_point(
            db,
            zone.fulfilment_provider if zone else None,
            float(latitude),
            float(longitude),
            address,
            # Costed from the kitchen that will actually bake it, not from
            # whichever branch happens to be first in the list.
            zone.branch_id if zone else None,
        )
        if estimate is not None or error is not None:
            await lalamove_service.record_cart_estimate(
                db,
                cart,
                zone=zone,
                # The fee actually charged, free delivery included, because the
                # comparison that matters is cost against revenue.
                fee=Decimal("0.00") if qualifies else base_fee,
                latitude=float(latitude),
                longitude=float(longitude),
                estimate=estimate,
                error=error,
            )

    return {
        "delivery_fee": float(Decimal("0.00") if qualifies else base_fee),
        # What it would have cost, so the summary can strike it through and
        # show the customer the saving they just earned.
        "base_fee": float(base_fee),
        "free_delivery_applied": qualifies,
        "free_threshold": float(settings.free_delivery_threshold),
        # Zero once it is free, however it became free. Otherwise a trial
        # account would be shown "free delivery" and "AED 150 to go" at once.
        "remaining_for_free": 0.0
        if qualifies
        else float(max(Decimal("0.00"), settings.free_delivery_threshold - subtotal)),
        "zone_name": zone.name if zone else None,
        "in_known_zone": zone is not None,
        # How long it takes, where we can say. A noon Send zone dispatches the
        # moment an order is packed and the rider is already nearby, so an hour
        # is a promise we can keep. Every other zone either waits for a batch
        # window or is driven by hand, and no honest number exists for those —
        # so the field is absent rather than guessed, and the storefront shows
        # nothing at all.
        "estimated_delivery_minutes": (
            60 if zone is not None and zone.is_noon_send else None
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
