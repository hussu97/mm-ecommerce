"""
Admin control of the delivery map: what each zone costs and who carries it.

Maps are versioned and a published one is read-only. Changing a fee means
cloning the live map into a draft, editing the draft, and publishing it —
which makes rolling back a single click on yesterday's version rather than an
attempt to remember what the numbers used to be.

Geometry is deliberately absent from the list responses. The Abu Dhabi outline
alone is four and a half thousand points, and an admin comparing fees does not
need to download the coastline to do it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_admin_user, get_db
from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
    FulfilmentProviderEnum,
)
from app.models.user import User
from app.services import audit_service, delivery_service, delivery_zone_service

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class PolygonResponse(BaseModel):
    id: str
    name: str
    region_slug: str | None
    delivery_fee: float
    fulfilment_provider: str
    display_order: int
    #: How many coordinates the outline has, so the admin can tell a hand-drawn
    #: box from a real boundary without fetching either.
    point_count: int

    @classmethod
    def of(cls, p: DeliveryPolygon) -> "PolygonResponse":
        return cls(
            id=str(p.id),
            name=p.name,
            region_slug=p.region_slug,
            delivery_fee=float(p.delivery_fee),
            fulfilment_provider=p.fulfilment_provider,
            display_order=p.display_order,
            point_count=_point_count(p.geometry),
        )


class VersionResponse(BaseModel):
    id: str
    name: str
    notes: str | None
    is_active: bool
    created_at: str
    activated_at: str | None
    polygons: list[PolygonResponse]

    @classmethod
    def of(cls, v: DeliveryPolygonVersion) -> "VersionResponse":
        return cls(
            id=str(v.id),
            name=v.name,
            notes=v.notes,
            is_active=v.is_active,
            created_at=v.created_at.isoformat(),
            activated_at=v.activated_at.isoformat() if v.activated_at else None,
            polygons=[
                PolygonResponse.of(p)
                for p in sorted(v.polygons, key=lambda p: p.display_order)
            ],
        )


class VersionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str | None = None
    #: Which map to copy. Defaults to the live one, because a draft almost
    #: always starts as "the current prices, with one of them changed".
    source_version_id: uuid.UUID | None = None


class PolygonUpdate(BaseModel):
    delivery_fee: Decimal | None = Field(None, ge=0)
    fulfilment_provider: str | None = None
    display_order: int | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _point_count(geometry: Any) -> int:
    if not isinstance(geometry, dict):
        return 0
    polys = (
        geometry.get("coordinates") or []
        if geometry.get("type") == "MultiPolygon"
        else [geometry.get("coordinates") or []]
    )
    return sum(len(ring) for poly in polys for ring in poly)


async def _load_version(
    db: AsyncSession, version_id: uuid.UUID
) -> DeliveryPolygonVersion:
    result = await db.execute(
        select(DeliveryPolygonVersion)
        .options(selectinload(DeliveryPolygonVersion.polygons))
        .where(DeliveryPolygonVersion.id == version_id)
    )
    version = result.scalars().first()
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery map not found")
    return version


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/versions", response_model=list[VersionResponse])
async def list_versions(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Every delivery map, newest first. The live one is flagged."""
    result = await db.execute(
        select(DeliveryPolygonVersion)
        .options(selectinload(DeliveryPolygonVersion.polygons))
        .order_by(DeliveryPolygonVersion.created_at.desc())
    )
    return [VersionResponse.of(v) for v in result.scalars().all()]


@router.get("/polygons/{polygon_id}/geometry", response_model=dict[str, Any])
async def get_polygon_geometry(
    polygon_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """The GeoJSON outline of one zone, for drawing it on a map."""
    result = await db.execute(
        select(DeliveryPolygon).where(DeliveryPolygon.id == polygon_id)
    )
    polygon = result.scalars().first()
    if polygon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return {
        "name": polygon.name,
        "delivery_fee": float(polygon.delivery_fee),
        "fulfilment_provider": polygon.fulfilment_provider,
        "geometry": polygon.geometry,
    }


@router.post(
    "/versions", response_model=VersionResponse, status_code=status.HTTP_201_CREATED
)
async def create_version(
    data: VersionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Copy a map into a new draft.

    The copy is where fees and couriers get changed. It is not live until it is
    published, so a half-edited map never prices an order.
    """
    source = (
        await _load_version(db, data.source_version_id)
        if data.source_version_id
        else await delivery_zone_service.get_active_version(db)
    )
    if source is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "There is no map to copy. Publish one first.",
        )
    if not source.polygons:
        source = await _load_version(db, source.id)

    draft = DeliveryPolygonVersion(name=data.name, notes=data.notes, is_active=False)
    db.add(draft)
    await db.flush()

    for polygon in source.polygons:
        db.add(
            DeliveryPolygon(
                version_id=draft.id,
                name=polygon.name,
                region_slug=polygon.region_slug,
                delivery_fee=polygon.delivery_fee,
                fulfilment_provider=polygon.fulfilment_provider,
                geometry=polygon.geometry,
                min_lat=polygon.min_lat,
                max_lat=polygon.max_lat,
                min_lng=polygon.min_lng,
                max_lng=polygon.max_lng,
                display_order=polygon.display_order,
            )
        )
    await db.flush()

    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="delivery_map",
        entity_id=str(draft.id),
        entity_label=draft.name,
        admin=admin,
        changes={"copied_from": source.name},
        request=request,
    )
    return VersionResponse.of(await _load_version(db, draft.id))


@router.put("/polygons/{polygon_id}", response_model=PolygonResponse)
async def update_polygon(
    polygon_id: uuid.UUID,
    data: PolygonUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Change a draft zone's fee, courier or precedence.

    Refuses to touch the live map. Editing prices under the storefront's feet
    is exactly the failure the versioning exists to prevent — clone it, change
    the copy, publish the copy.
    """
    result = await db.execute(
        select(DeliveryPolygon)
        .options(selectinload(DeliveryPolygon.version))
        .where(DeliveryPolygon.id == polygon_id)
    )
    polygon = result.scalars().first()
    if polygon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    if polygon.version.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This map is live. Copy it to a draft, edit the draft, then publish it.",
        )

    before = {
        "delivery_fee": float(polygon.delivery_fee),
        "fulfilment_provider": polygon.fulfilment_provider,
        "display_order": polygon.display_order,
    }

    if data.delivery_fee is not None:
        polygon.delivery_fee = data.delivery_fee
    if data.fulfilment_provider is not None:
        allowed = {p.value for p in FulfilmentProviderEnum}
        if data.fulfilment_provider not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown courier '{data.fulfilment_provider}'. "
                f"Choose one of: {', '.join(sorted(allowed))}",
            )
        polygon.fulfilment_provider = data.fulfilment_provider
    if data.display_order is not None:
        polygon.display_order = data.display_order

    await db.flush()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_zone",
        entity_id=str(polygon.id),
        entity_label=f"{polygon.version.name} · {polygon.name}",
        admin=admin,
        changes={
            "from": before,
            "to": {
                "delivery_fee": float(polygon.delivery_fee),
                "fulfilment_provider": polygon.fulfilment_provider,
                "display_order": polygon.display_order,
            },
        },
        request=request,
    )
    return PolygonResponse.of(polygon)


@router.post("/versions/{version_id}/activate", response_model=VersionResponse)
async def activate_version(
    version_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Make this map the live one. Also how a rollback is done.

    Only one map can be active — the database enforces it — so the outgoing one
    is stepped down in the same transaction.
    """
    version = await _load_version(db, version_id)
    if not version.polygons:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This map has no zones. Publishing it would price every address at "
            "the default fee.",
        )

    previous = await delivery_zone_service.get_active_version(db)
    if previous is not None and previous.id == version.id:
        return VersionResponse.of(version)

    if previous is not None:
        previous.is_active = False
        await db.flush()

    version.is_active = True
    version.activated_at = datetime.now(timezone.utc)
    await db.flush()
    delivery_zone_service.invalidate_cache()

    await audit_service.log_action(
        db,
        action="PUBLISH",
        entity_type="delivery_map",
        entity_id=str(version.id),
        entity_label=version.name,
        admin=admin,
        changes={"from": previous.name if previous else None, "to": version.name},
        request=request,
    )
    return VersionResponse.of(version)


@router.delete("/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(
    version_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Throw away a draft. The live map cannot be deleted."""
    version = await _load_version(db, version_id)
    if version.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This map is live. Publish another one before deleting it.",
        )
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="delivery_map",
        entity_id=str(version.id),
        entity_label=version.name,
        admin=admin,
        request=request,
    )
    await db.delete(version)


@router.get("/summary", response_model=dict[str, Any])
async def zone_summary(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """The live map at a glance, with the settings that apply to every zone."""
    version = await delivery_zone_service.get_active_version(db)
    zones = await delivery_zone_service.get_active_zones(db)
    settings = await delivery_service.get_settings(db)
    return {
        "version": {"id": str(version.id), "name": version.name} if version else None,
        "zones": [
            {
                "name": z.name,
                "region_slug": z.region_slug,
                "delivery_fee": float(z.delivery_fee),
                "fulfilment_provider": z.fulfilment_provider,
            }
            for z in zones
        ],
        # Unchanged by the zone map: the threshold is the same everywhere, so a
        # customer in Fujairah earns free delivery on the same basket as one in
        # Sharjah.
        "free_threshold": float(settings.free_delivery_threshold),
        "default_delivery_fee": float(settings.default_delivery_fee),
        "pickup_fee": float(settings.pickup_fee),
    }
