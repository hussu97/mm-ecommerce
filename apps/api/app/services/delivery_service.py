from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import DeliveryMethodEnum
from app.models.region import DeliverySettings, Region


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
        )
    return settings


async def calculate_fee(
    delivery_method: DeliveryMethodEnum,
    region_slug: str | None,
    subtotal: Decimal,
    db: AsyncSession,
    settings: DeliverySettings | None = None,
) -> Decimal:
    """Return the delivery fee in AED, reading rates from the database."""
    if settings is None:
        settings = await get_settings(db)

    if delivery_method == DeliveryMethodEnum.PICKUP:
        return settings.pickup_fee

    if subtotal >= settings.free_delivery_threshold:
        return Decimal("0.00")

    if region_slug:
        result = await db.execute(
            select(Region).where(Region.slug == region_slug, Region.is_active == True)  # noqa: E712
        )
        region = result.scalars().first()
        if region:
            return region.delivery_fee

    # Inactive / unknown region — charge the highest fee among active regions
    result = await db.execute(
        select(Region.delivery_fee)
        .where(Region.is_active == True)  # noqa: E712
        .order_by(Region.delivery_fee.desc())
    )
    max_fee = result.scalars().first()
    return max_fee if max_fee is not None else Decimal("50.00")


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
    }
