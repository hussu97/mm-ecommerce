from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import DeliveryMethodEnum
from app.models.region import DeliverySettings, Region
from app.services import delivery_zone_service

__all__ = [
    "calculate_fee",
    "quote",
    "get_active_regions",
    "get_all_regions",
    "get_delivery_rates",
    "get_settings",
]


async def get_active_regions(db: AsyncSession) -> list[Region]:
    result = await db.execute(
        select(Region).where(Region.is_active == True).order_by(Region.sort_order)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_all_regions(db: AsyncSession) -> list[Region]:
    result = await db.execute(select(Region).order_by(Region.sort_order))
    return list(result.scalars().all())


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
    region_slug: str | None,
    subtotal: Decimal,
    db: AsyncSession,
    settings: DeliverySettings | None = None,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
) -> Decimal:
    """
    The delivery fee in AED.

    The pin decides it. `region_slug` is only consulted for orders that predate
    the polygon map or arrive without coordinates, because a self-declared
    emirate was always a guess — wrong for an address a few hundred metres over
    a boundary, and wrong for everyone who left the dropdown on its default.
    """
    if settings is None:
        settings = await get_settings(db)

    if delivery_method == DeliveryMethodEnum.PICKUP:
        return settings.pickup_fee

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

    if region_slug:
        result = await db.execute(
            select(Region).where(Region.slug == region_slug, Region.is_active == True)  # noqa: E712
        )
        region = result.scalars().first()
        if region:
            return region.delivery_fee

    return settings.default_delivery_fee


async def quote(
    db: AsyncSession,
    subtotal: Decimal,
    latitude: Decimal | float | None = None,
    longitude: Decimal | float | None = None,
) -> dict:
    """What delivery would cost to this point, for the checkout to show live."""
    settings = await get_settings(db)
    zone = (
        await delivery_zone_service.find_zone(db, float(latitude), float(longitude))
        if latitude is not None and longitude is not None
        else None
    )
    base_fee = zone.delivery_fee if zone else settings.default_delivery_fee
    qualifies = subtotal >= settings.free_delivery_threshold
    return {
        "delivery_fee": float(Decimal("0.00") if qualifies else base_fee),
        # What it would have cost, so the summary can strike it through and
        # show the customer the saving they just earned.
        "base_fee": float(base_fee),
        "free_delivery_applied": qualifies,
        "free_threshold": float(settings.free_delivery_threshold),
        "remaining_for_free": float(
            max(Decimal("0.00"), settings.free_delivery_threshold - subtotal)
        ),
        "zone_name": zone.name if zone else None,
        "region_slug": zone.region_slug if zone else None,
        "in_known_zone": zone is not None,
    }


async def get_delivery_rates(db: AsyncSession) -> dict:
    """Return a serialisable summary of all active regions and their fees."""
    regions = await get_active_regions(db)
    settings = await get_settings(db)
    return {
        "regions": [
            {
                "slug": r.slug,
                "name_translations": r.name_translations,
                "delivery_fee": float(r.delivery_fee),
            }
            for r in regions
        ],
        "free_threshold": float(settings.free_delivery_threshold),
        "pickup_fee": float(settings.pickup_fee),
        "default_delivery_fee": float(settings.default_delivery_fee),
    }
