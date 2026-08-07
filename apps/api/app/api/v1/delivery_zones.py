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
from app.models.branch import Branch
from app.models.delivery_batch import (
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchWindow,
)
from app.models.delivery_settings import DeliverySettings
from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
    DeliveryPricingEnum,
    FulfilmentProviderEnum,
)
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.user import User
from app.services import (
    audit_service,
    batching_service,
    delivery_service,
    delivery_zone_service,
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────


class PolygonResponse(BaseModel):
    id: str
    name: str
    #: Only meaningful when `pricing_mode` is static. A dynamic zone charges the
    #: courier's own quote for the customer's pin and never reads this.
    delivery_fee: float
    pricing_mode: str
    #: Whether a qualifying basket delivers free here. Independent of the fee
    #: and of the courier: a fixed-fee third-party zone is not automatically an
    #: offer, and reading it off either of those was how it last went wrong.
    free_delivery_eligible: bool
    #: The basket that earns free delivery here. Null means "use the national
    #: threshold" — which is what every zone meant before thresholds could vary.
    #: Zero is different from null: it means free at any basket.
    free_delivery_threshold: float | None
    fulfilment_provider: str
    #: The kitchen that bakes this zone's orders and hands them to the courier.
    #: Null falls back to the single configured pickup branch.
    branch_id: str | None
    display_order: int
    #: How many coordinates the outline has, so the admin can tell a hand-drawn
    #: box from a real boundary without fetching either.
    point_count: int

    @classmethod
    def of(cls, p: DeliveryPolygon) -> "PolygonResponse":
        return cls(
            id=str(p.id),
            name=p.name,
            delivery_fee=float(p.delivery_fee),
            pricing_mode=p.pricing_mode,
            free_delivery_eligible=p.free_delivery_eligible,
            free_delivery_threshold=(
                None
                if p.free_delivery_threshold is None
                else float(p.free_delivery_threshold)
            ),
            fulfilment_provider=p.fulfilment_provider,
            branch_id=str(p.branch_id) if p.branch_id else None,
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
    pricing_mode: str | None = None
    free_delivery_eligible: bool | None = None
    #: Null is a real instruction here — "clear this zone's own threshold and go
    #: back to the national one" — so unlike every other field on this model,
    #: omitted and null are not the same. The handler reads `model_fields_set`
    #: to tell them apart rather than testing `is not None`.
    free_delivery_threshold: Decimal | None = Field(None, ge=0)
    fulfilment_provider: str | None = None
    branch_id: uuid.UUID | None = None
    display_order: int | None = None


class BatchWindowResponse(BaseModel):
    id: str
    polygon_id: str
    label: str
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    is_active: bool
    #: True for a window like 22:00–02:00, so the admin can see at a glance
    #: that it belongs to two calendar days.
    wraps_midnight: bool

    @classmethod
    def of(cls, w: DeliveryBatchWindow) -> "BatchWindowResponse":
        return cls(
            id=str(w.id),
            polygon_id=str(w.polygon_id),
            label=w.label,
            start_hour=w.start_hour,
            start_minute=w.start_minute,
            end_hour=w.end_hour,
            end_minute=w.end_minute,
            is_active=w.is_active,
            wraps_midnight=w.wraps_midnight,
        )


class BatchWindowWrite(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    start_hour: int = Field(ge=0, le=23)
    start_minute: int = Field(default=0, ge=0, le=59)
    #: 24 means midnight closing the day, so 23:00–24:00 can be written without
    #: pretending it runs into tomorrow.
    end_hour: int = Field(ge=0, le=24)
    end_minute: int = Field(default=0, ge=0, le=59)
    is_active: bool = True


class BatchResponse(BaseModel):
    id: str
    #: The zone whose slot opened this run. A run is shared by every zone
    #: closing on the same minute, so it says where the run came from rather
    #: than what is on it — `zone_name` is the honest answer to that.
    polygon_id: str
    #: Every zone with an order on this run, comma-separated.
    zone_name: str | None
    window_label: str | None
    dispatch_at: datetime
    status: str
    stop_count: int
    courier_order_id: str | None
    courier_status: str | None
    share_link: str | None
    driver_name: str | None
    distance_m: int | None
    cost_total: float | None
    #: What the run worked out at per order. The number the whole feature
    #: exists to move.
    cost_per_delivery: float | None
    dispatched_at: datetime | None
    last_error: str | None
    #: How many times this run has been offered to the courier.
    attempt_count: int
    #: When it will be offered again on its own. Null means nothing more will
    #: happen without somebody pressing the button — either it went out, or
    #: another attempt cannot change the answer.
    next_attempt_at: datetime | None
    order_numbers: list[str]

    @classmethod
    def of(
        cls, b: DeliveryBatch, zone_name: str | None, order_numbers: list[str]
    ) -> "BatchResponse":
        per = b.cost_per_delivery
        return cls(
            id=str(b.id),
            polygon_id=str(b.polygon_id),
            zone_name=zone_name,
            window_label=b.window_label,
            dispatch_at=b.dispatch_at,
            status=b.status,
            stop_count=b.stop_count,
            courier_order_id=b.courier_order_id,
            courier_status=b.courier_status,
            share_link=b.share_link,
            driver_name=b.driver_name,
            distance_m=b.distance_m,
            cost_total=float(b.cost_total) if b.cost_total is not None else None,
            cost_per_delivery=float(per) if per is not None else None,
            dispatched_at=b.dispatched_at,
            last_error=b.last_error,
            attempt_count=b.attempt_count,
            next_attempt_at=b.next_attempt_at,
            order_numbers=order_numbers,
        )


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


def _simplify(geometry: Any, tolerance: float) -> Any:
    """
    Drop coordinates that a screen cannot tell apart.

    The seven emirate outlines together are about eight thousand points, most
    of them describing bays and sandbanks a few metres across. At the scale a
    country fits on a monitor, a tenth of those draw the same picture. Rings
    that collapse to fewer than four points are dropped whole — they were
    islands smaller than a pixel.

    Radial distance, not Douglas–Peucker: it is a single pass, it never moves a
    point that survives, and the error it can introduce is bounded by the
    tolerance itself. Only ever used for display; pricing reads the full shape.
    """
    if not isinstance(geometry, dict):
        return geometry
    polys = (
        geometry.get("coordinates") or []
        if geometry.get("type") == "MultiPolygon"
        else [geometry.get("coordinates") or []]
    )

    kept_polys = []
    for poly in polys:
        rings = []
        for ring in poly:
            if len(ring) < 4:
                continue
            thinned = [ring[0]]
            for point in ring[1:-1]:
                last = thinned[-1]
                if (
                    abs(point[0] - last[0]) > tolerance
                    or abs(point[1] - last[1]) > tolerance
                ):
                    thinned.append(point)
            thinned.append(ring[-1] if ring[-1] != ring[0] else ring[0])
            if len(thinned) >= 4:
                rings.append(thinned)
        if rings:
            kept_polys.append(rings)

    return {"type": "MultiPolygon", "coordinates": kept_polys}


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


@router.get("/map", response_model=dict[str, Any])
async def zone_map(
    version_id: uuid.UUID | None = None,
    #: Degrees. About 550 m at this latitude — invisible on a map of the whole
    #: country, and it turns eight thousand points into a few hundred.
    tolerance: float = 0.005,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """
    Every zone on a map, as drawable outlines with their fee and courier.

    One call rather than one per zone: the admin draws the whole country at
    once and there is nothing to show until the last shape arrives anyway.
    Simplified for display only — the fee an order pays is always resolved
    against the full outline.
    """
    if version_id is None:
        active = await delivery_zone_service.get_active_version(db)
        if active is None:
            return {"version": None, "zones": [], "bounds": None}
        version_id = active.id
    # Always reloaded with its polygons attached. `get_active_version` returns a
    # bare row, and reaching for `.polygons` on it would be a lazy load inside
    # an async session — which is not a slow query, it is an exception.
    version = await _load_version(db, version_id)

    zones = []
    lats: list[float] = []
    lngs: list[float] = []
    for polygon in sorted(version.polygons, key=lambda p: p.display_order):
        zones.append(
            {
                "id": str(polygon.id),
                "name": polygon.name,
                "delivery_fee": float(polygon.delivery_fee),
                "pricing_mode": polygon.pricing_mode,
                "free_delivery_eligible": polygon.free_delivery_eligible,
                "free_delivery_threshold": (
                    None
                    if polygon.free_delivery_threshold is None
                    else float(polygon.free_delivery_threshold)
                ),
                "fulfilment_provider": polygon.fulfilment_provider,
                "display_order": polygon.display_order,
                "geometry": _simplify(polygon.geometry, tolerance),
            }
        )
        lats += [float(polygon.min_lat), float(polygon.max_lat)]
        lngs += [float(polygon.min_lng), float(polygon.max_lng)]

    return {
        "version": {"id": str(version.id), "name": version.name},
        "zones": zones,
        # The stored bounding boxes, so the client can frame the country
        # without walking every coordinate it was just sent.
        "bounds": {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lng": min(lngs),
            "max_lng": max(lngs),
        }
        if lats
        else None,
    }


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
        "pricing_mode": polygon.pricing_mode,
        "free_delivery_eligible": polygon.free_delivery_eligible,
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
    source_id = data.source_version_id
    if source_id is None:
        active = await delivery_zone_service.get_active_version(db)
        if active is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "There is no map to copy. Publish one first.",
            )
        source_id = active.id
    # Reloaded rather than reused: the active version arrives without its
    # polygons, and touching them lazily inside an async session raises.
    source = await _load_version(db, source_id)

    draft = DeliveryPolygonVersion(name=data.name, notes=data.notes, is_active=False)
    db.add(draft)
    await db.flush()

    for polygon in source.polygons:
        copy = DeliveryPolygon(
            version_id=draft.id,
            name=polygon.name,
            delivery_fee=polygon.delivery_fee,
            pricing_mode=polygon.pricing_mode,
            free_delivery_eligible=polygon.free_delivery_eligible,
            # And so does the threshold, for the third time in this list and the
            # same reason: a draft that loses it does not fail, it quietly
            # reverts every zone to the national number — giving delivery away
            # in the far zones and withholding it in the near ones, with nothing
            # on screen to say the map changed.
            free_delivery_threshold=polygon.free_delivery_threshold,
            fulfilment_provider=polygon.fulfilment_provider,
            # The kitchen travels with the zone for the same reason the schedule
            # does: a draft published without it points every zone at nothing,
            # and every website order lands on no register at all — silently,
            # because the order itself is perfectly fine.
            branch_id=polygon.branch_id,
            geometry=polygon.geometry,
            min_lat=polygon.min_lat,
            max_lat=polygon.max_lat,
            min_lng=polygon.min_lng,
            max_lng=polygon.max_lng,
            display_order=polygon.display_order,
        )
        db.add(copy)
        await db.flush()
        # The batch schedule travels with the zone. A draft published without
        # one would silently stop batching and start sending every order on its
        # own — the same orders, at three times the cost, with nothing on
        # screen to say why.
        for window in await _windows_of(db, polygon.id):
            db.add(
                DeliveryBatchWindow(
                    polygon_id=copy.id,
                    label=window.label,
                    start_hour=window.start_hour,
                    start_minute=window.start_minute,
                    end_hour=window.end_hour,
                    end_minute=window.end_minute,
                    is_active=window.is_active,
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
        "pricing_mode": polygon.pricing_mode,
        "free_delivery_eligible": polygon.free_delivery_eligible,
        "free_delivery_threshold": (
            None
            if polygon.free_delivery_threshold is None
            else float(polygon.free_delivery_threshold)
        ),
        "fulfilment_provider": polygon.fulfilment_provider,
        "branch_id": str(polygon.branch_id) if polygon.branch_id else None,
        "display_order": polygon.display_order,
    }

    if data.delivery_fee is not None:
        polygon.delivery_fee = data.delivery_fee
    if data.pricing_mode is not None:
        modes = {m.value for m in DeliveryPricingEnum}
        if data.pricing_mode not in modes:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown pricing mode '{data.pricing_mode}'. "
                f"Choose one of: {', '.join(sorted(modes))}",
            )
        polygon.pricing_mode = data.pricing_mode
        if polygon.pricing_mode == DeliveryPricingEnum.DYNAMIC.value:
            # Nothing reads the fee once the courier sets it, and a stale number
            # left in the column is how someone later reads the table as "we
            # charge 50 here".
            polygon.delivery_fee = Decimal("0.00")
    if data.free_delivery_eligible is not None:
        polygon.free_delivery_eligible = data.free_delivery_eligible
    if "free_delivery_threshold" in data.model_fields_set:
        # Null clears the override; the zone goes back to the national number.
        polygon.free_delivery_threshold = data.free_delivery_threshold
    if data.fulfilment_provider is not None:
        allowed = {p.value for p in FulfilmentProviderEnum}
        if data.fulfilment_provider not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown courier '{data.fulfilment_provider}'. "
                f"Choose one of: {', '.join(sorted(allowed))}",
            )
        polygon.fulfilment_provider = data.fulfilment_provider
    if data.branch_id is not None:
        branch = await db.get(Branch, data.branch_id)
        if branch is None or branch.deleted_at is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That branch does not exist, so nothing could bake this zone.",
            )
        polygon.branch_id = data.branch_id
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
                "pricing_mode": polygon.pricing_mode,
                "free_delivery_eligible": polygon.free_delivery_eligible,
                "free_delivery_threshold": (
                    None
                    if polygon.free_delivery_threshold is None
                    else float(polygon.free_delivery_threshold)
                ),
                "fulfilment_provider": polygon.fulfilment_provider,
                "branch_id": str(polygon.branch_id) if polygon.branch_id else None,
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
                # Zero on a courier-priced zone, and meaningless there — the fee
                # comes from the pin. `pricing_mode` is what says which it is.
                "delivery_fee": float(z.delivery_fee),
                "pricing_mode": z.pricing_mode,
                "free_delivery_eligible": z.free_delivery_eligible,
                "free_delivery_threshold": (
                    None
                    if z.free_delivery_threshold is None
                    else float(z.free_delivery_threshold)
                ),
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


# ── Batching ──────────────────────────────────────────────────────────────────


async def _load_polygon(db: AsyncSession, polygon_id: uuid.UUID) -> DeliveryPolygon:
    polygon = await db.get(DeliveryPolygon, polygon_id)
    if polygon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return polygon


async def _windows_of(
    db: AsyncSession, polygon_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    result = await db.execute(
        select(DeliveryBatchWindow)
        .where(DeliveryBatchWindow.polygon_id == polygon_id)
        .order_by(DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute)
    )
    return list(result.scalars().all())


def _reject_overlaps(windows: list[DeliveryBatchWindow]) -> None:
    clash = batching_service.overlapping(windows)
    if clash is None:
        return
    first, second = clash
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        f'"{first.label}" and "{second.label}" both cover the same time. '
        "Two batches claiming one minute makes it a coin toss which run an "
        "order joins.",
    )


@router.get(
    "/polygons/{polygon_id}/batch-windows", response_model=list[BatchWindowResponse]
)
async def list_batch_windows(
    polygon_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """When orders in this zone travel together. All times are Dubai time."""
    await _load_polygon(db, polygon_id)
    return [BatchWindowResponse.of(w) for w in await _windows_of(db, polygon_id)]


@router.post(
    "/polygons/{polygon_id}/batch-windows",
    response_model=BatchWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_window(
    polygon_id: uuid.UUID,
    data: BatchWindowWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Add a slot.

    Only a Lalamove zone can have one. A third-party zone has no run of ours to
    share at all, and noon Send's shared run is a separate product we do not
    use — so a schedule on either would be a setting that does nothing, which
    is worse than an absent one because somebody will come to rely on it.
    """
    polygon = await _load_polygon(db, polygon_id)
    if polygon.fulfilment_provider != FulfilmentProviderEnum.LALAMOVE.value:
        reason = (
            "noon Send's shared runs are a different product with their own "
            "endpoint and a cap of three drops, which we do not use"
            if polygon.fulfilment_provider == FulfilmentProviderEnum.NOON_SEND.value
            else "it is delivered by a third party, so there is no run of ours "
            "for its orders to share"
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{polygon.name}' cannot be batched: {reason}.",
        )

    window = DeliveryBatchWindow(polygon_id=polygon_id, **data.model_dump())
    _reject_overlaps([*await _windows_of(db, polygon_id), window])
    db.add(window)
    await db.flush()

    # Anything already waiting is re-derived, so adding a slot picks up the
    # orders that fell into the gap it just filled.
    await batching_service.reschedule_polygon(db, polygon_id)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=f"{polygon.name} · {window.label}",
        admin=admin,
        changes=data.model_dump(),
        request=request,
    )
    return BatchWindowResponse.of(window)


@router.put("/batch-windows/{window_id}", response_model=BatchWindowResponse)
async def update_batch_window(
    window_id: uuid.UUID,
    data: BatchWindowWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Move a slot, and move everything still waiting on it.

    An order whose new window has already closed goes out on its own rather
    than waiting until tomorrow for a slot that has been and gone.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch window not found")

    before = BatchWindowResponse.of(window).model_dump()
    for field, value in data.model_dump().items():
        setattr(window, field, value)
    _reject_overlaps(await _windows_of(db, window.polygon_id))
    await db.flush()

    moved = await batching_service.reschedule_polygon(db, window.polygon_id)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=window.label,
        admin=admin,
        changes={"from": before, "to": data.model_dump(), "rescheduled": moved},
        request=request,
    )
    return BatchWindowResponse.of(window)


@router.delete("/batch-windows/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch_window(
    window_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Remove a slot. Orders waiting on it are re-derived, and any that no longer
    fall in a window go out on their own rather than being stranded.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch window not found")

    polygon_id = window.polygon_id
    label = window.label
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=label,
        admin=admin,
        request=request,
    )
    await db.delete(window)
    await db.flush()
    await batching_service.reschedule_polygon(db, polygon_id)


@router.get("/batches", response_model=list[BatchResponse])
async def list_batches(
    status_filter: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Runs waiting to leave and runs already gone, most imminent first."""
    stmt = (
        select(DeliveryBatch)
        .order_by(DeliveryBatch.dispatch_at.desc())
        .limit(min(limit, 200))
    )
    if status_filter:
        stmt = stmt.where(DeliveryBatch.status == status_filter)
    batches = list((await db.execute(stmt)).scalars().all())
    if not batches:
        return []

    zone_names = dict(
        (
            await db.execute(
                select(DeliveryPolygon.id, DeliveryPolygon.name).where(
                    DeliveryPolygon.id.in_({b.polygon_id for b in batches})
                )
            )
        ).all()
    )
    rows = (
        await db.execute(
            select(OrderDelivery.batch_id, OrderDelivery.zone_name, Order.order_number)
            .join(Order, Order.id == OrderDelivery.order_id)
            .where(OrderDelivery.batch_id.in_({b.id for b in batches}))
            .order_by(OrderDelivery.stop_sequence, Order.order_number)
        )
    ).all()
    numbers: dict[uuid.UUID, list[str]] = {}
    on_run: dict[uuid.UUID, list[str]] = {}
    for batch_id, zone_name, order_number in rows:
        numbers.setdefault(batch_id, []).append(order_number)
        zones_here = on_run.setdefault(batch_id, [])
        if zone_name and zone_name not in zones_here:
            zones_here.append(zone_name)

    return [
        BatchResponse.of(
            b,
            # What is actually on the run. Falls back to the zone that opened it
            # for a batch that has not collected anything yet.
            ", ".join(on_run.get(b.id) or []) or zone_names.get(b.polygon_id),
            numbers.get(b.id, []),
        )
        for b in batches
    ]


@router.post("/batches/{batch_id}/dispatch", response_model=BatchResponse)
async def dispatch_batch_now(
    batch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Send a run early, or retry one the courier refused.

    The sweeper fires these on schedule and retries a refusal a few times on its
    own; this is for the shop deciding it is not worth waiting, and for the run
    that has already exhausted those attempts.

    Pressing it resets the ladder. Somebody doing this by hand has almost always
    just changed something the automatic attempts could not — topped up the
    wallet, fixed an address — so the run deserves the full set of tries again
    rather than the one that was left.
    """
    batch = await db.get(DeliveryBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Batch not found")
    if batch.status == BatchStatusEnum.DISPATCHED.value and not batch.next_attempt_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "This run has already gone out.")

    batch.attempt_count = 0
    await batching_service.dispatch_batch(db, batch)
    await db.flush()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch",
        entity_id=str(batch.id),
        entity_label=f"{batch.window_label or 'run'} · {batch.stop_count} drops",
        admin=admin,
        changes={
            "courier_order_id": batch.courier_order_id,
            "status": batch.status,
            "error": batch.last_error,
        },
        request=request,
    )
    polygon = await db.get(DeliveryPolygon, batch.polygon_id)
    return BatchResponse.of(batch, polygon.name if polygon else None, [])


# ── Settings ──────────────────────────────────────────────────────────────────


class DeliverySettingsResponse(BaseModel):
    id: str
    free_delivery_threshold: float
    pickup_fee: float
    default_delivery_fee: float
    #: The small-basket surcharge, and the basket at or below which it applies.
    #: Both live here rather than in code because they are commercial numbers
    #: that get argued with, and the storefront reads them from
    #: `/delivery/rates` so there is exactly one place to change them.
    low_order_fee: float
    #: Null switches the fee off entirely — which is not the same as a
    #: threshold of zero, and the admin form has to keep them distinguishable.
    low_order_threshold: float | None

    @classmethod
    def of(cls, s: DeliverySettings) -> "DeliverySettingsResponse":
        return cls(
            id=str(s.id),
            free_delivery_threshold=float(s.free_delivery_threshold),
            pickup_fee=float(s.pickup_fee),
            default_delivery_fee=float(s.default_delivery_fee),
            low_order_fee=float(s.low_order_fee or 0),
            low_order_threshold=(
                None if s.low_order_threshold is None else float(s.low_order_threshold)
            ),
        )


class DeliverySettingsUpdate(BaseModel):
    free_delivery_threshold: Decimal | None = Field(None, ge=0)
    pickup_fee: Decimal | None = Field(None, ge=0)
    default_delivery_fee: Decimal | None = Field(None, ge=0)
    low_order_fee: Decimal | None = Field(None, ge=0)
    #: Null is a real instruction here — "switch the fee off" — so unlike the
    #: fields above, omitted and null are not the same. The handler reads
    #: `model_fields_set` to tell them apart.
    low_order_threshold: Decimal | None = Field(None, ge=0)


@router.get("/settings", response_model=DeliverySettingsResponse)
async def get_delivery_settings(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """
    The three delivery numbers that are not a property of any zone.

    They live here rather than on their own screen because there is nothing
    else left to configure about delivery: the free-delivery threshold is
    deliberately identical everywhere, pickup has no zone, and the default is
    what a pin outside every shape on the map gets charged.
    """
    return DeliverySettingsResponse.of(await delivery_service.get_settings(db))


@router.put("/settings", response_model=DeliverySettingsResponse)
async def update_delivery_settings(
    data: DeliverySettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Change them. Unlike a zone fee, these take effect immediately."""
    settings = await delivery_service.get_settings(db)
    before = DeliverySettingsResponse.of(settings).model_dump()

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(settings, field, value)
    # `exclude_none` above is right for every field except this one, where null
    # is a real instruction: it switches the small-basket fee off. Without this,
    # an admin could turn the fee on and never turn it back off.
    if "low_order_threshold" in data.model_fields_set:
        settings.low_order_threshold = data.low_order_threshold
    # `get_settings` invents a row when the table is empty, so this has to be an
    # add rather than an assumption that the object is already tracked.
    db.add(settings)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_settings",
        entity_id=str(settings.id),
        entity_label="Delivery settings",
        admin=admin,
        changes={
            "from": before,
            "to": DeliverySettingsResponse.of(settings).model_dump(),
        },
        request=request,
    )
    return DeliverySettingsResponse.of(settings)
