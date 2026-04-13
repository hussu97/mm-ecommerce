from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.region import DeliverySettings, Region
from app.models.user import User
from app.services import delivery_service

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class RegionResponse(BaseModel):
    id: str
    slug: str
    name_translations: dict[str, str]
    delivery_fee: float
    is_active: bool
    sort_order: int

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, r: Region) -> "RegionResponse":
        return cls(
            id=str(r.id),
            slug=r.slug,
            name_translations=r.name_translations,
            delivery_fee=float(r.delivery_fee),
            is_active=r.is_active,
            sort_order=r.sort_order,
        )


class RegionUpdate(BaseModel):
    name_translations: dict[str, str] | None = None
    delivery_fee: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None
    sort_order: int | None = None


class DeliverySettingsResponse(BaseModel):
    id: str
    free_delivery_threshold: float
    pickup_fee: float

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm(cls, s: DeliverySettings) -> "DeliverySettingsResponse":
        return cls(
            id=str(s.id),
            free_delivery_threshold=float(s.free_delivery_threshold),
            pickup_fee=float(s.pickup_fee),
        )


class DeliverySettingsUpdate(BaseModel):
    free_delivery_threshold: Decimal | None = Field(None, ge=0)
    pickup_fee: Decimal | None = Field(None, ge=0)


# ── Public endpoints (no auth) ────────────────────────────────────────────────


@router.get("/public", response_model=list[RegionResponse])
async def list_active_regions(db: AsyncSession = Depends(get_db)):
    """Return all active regions — used by the storefront address/checkout forms."""
    regions = await delivery_service.get_active_regions(db)
    return [RegionResponse.from_orm(r) for r in regions]


# ── Admin endpoints ───────────────────────────────────────────────────────────


@router.get("", response_model=list[RegionResponse])
async def admin_list_regions(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Admin: list all regions (active + inactive)."""
    regions = await delivery_service.get_all_regions(db)
    return [RegionResponse.from_orm(r) for r in regions]


@router.get("/settings", response_model=DeliverySettingsResponse)
async def admin_get_delivery_settings(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Admin: get global delivery settings (free threshold, pickup fee)."""
    settings = await delivery_service.get_settings(db)
    return DeliverySettingsResponse.from_orm(settings)


@router.put("/settings", response_model=DeliverySettingsResponse)
async def admin_update_delivery_settings(
    data: DeliverySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Admin: update global delivery settings."""
    settings = await delivery_service.get_settings(db)

    if data.free_delivery_threshold is not None:
        settings.free_delivery_threshold = data.free_delivery_threshold
    if data.pickup_fee is not None:
        settings.pickup_fee = data.pickup_fee

    # If settings row was ephemeral (not in DB), add it
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    return DeliverySettingsResponse.from_orm(settings)


@router.put("/{slug}", response_model=RegionResponse)
async def admin_update_region(
    slug: str,
    data: RegionUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Admin: update a region's name translations, fee, active status or sort order."""
    result = await db.execute(select(Region).where(Region.slug == slug))
    region = result.scalars().first()
    if region is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found."
        )

    if data.name_translations is not None:
        region.name_translations = data.name_translations
    if data.delivery_fee is not None:
        region.delivery_fee = data.delivery_fee
    if data.is_active is not None:
        region.is_active = data.is_active
    if data.sort_order is not None:
        region.sort_order = data.sort_order

    await db.commit()
    await db.refresh(region)
    return RegionResponse.from_orm(region)
