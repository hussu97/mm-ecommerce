"""
Map versions and the zones inside them.

A published map is read-only: changing a fee means cloning the live map into a
draft, editing the draft, and publishing it, so rolling back is a click on
yesterday's version rather than an attempt to remember the old numbers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.delivery_batch import DeliveryBatchGroup
from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
    DeliveryPricingEnum,
    FulfilmentProviderEnum,
)
from app.models.user import User
from app.services import (
    audit_service,
    courier_service,
    delivery_service,
    delivery_zone_service,
)

from .schemas import PolygonResponse, PolygonUpdate, VersionCreate, VersionResponse

router = APIRouter()


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
        raise NotFoundError("Delivery map not found")
    return version


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/versions", response_model=list[VersionResponse])
async def list_versions(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
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
    _admin: User = Depends(require("delivery.manage")),
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
                # NOT NULL since 088 — every zone answers for itself, because
                # the national number it used to fall back to is gone.
                "free_delivery_threshold": float(polygon.free_delivery_threshold),
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
    _admin: User = Depends(require("delivery.manage")),
):
    """The GeoJSON outline of one zone, for drawing it on a map."""
    result = await db.execute(
        select(DeliveryPolygon).where(DeliveryPolygon.id == polygon_id)
    )
    polygon = result.scalars().first()
    if polygon is None:
        raise NotFoundError("Zone not found")
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
    admin: User = Depends(require("delivery.manage")),
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
            raise BadRequestError(
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
            # And the alternates with it, for the fourth time in this list and
            # the same reason as the three above. A draft that loses them does
            # not fail: it publishes a map on which no order can be moved to
            # another courier at all, so the escape hatch is simply gone the
            # next time one is needed — with nothing on screen to say the map
            # changed. `list(...)` because the JSONB value is a mutable list
            # and sharing one between two rows makes editing the draft edit the
            # published map.
            alternate_providers=list(polygon.alternate_providers or []),
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
            # The run this zone travels on. A draft published without it does
            # not fail: it quietly stops batching and starts sending every order
            # on its own — the same orders, at several times the cost, with
            # nothing on screen to say why.
            #
            # The group itself is deliberately **not** copied. Groups are not
            # versioned — `DeliveryBatchWindow` says so in its own docstring —
            # because unlike a fee, a wrong window delays a dispatch by an hour
            # and is fixed by moving it, with anything still waiting rescheduled
            # onto the corrected slot. So the copy points at the same group the
            # source did, and `group_for_polygon` finds the same schedule
            # through it.
            #
            # There used to be a loop below here that copied the *windows* into
            # the draft, which read as though it covered this. It did not, and
            # could not: it passed a polygon id to `_windows_of`, which filters
            # on `group_id`, so it always matched nothing — and had it ever
            # matched, it built a `DeliveryBatchWindow(polygon_id=...)`, a
            # column that stopped existing when windows moved onto groups in
            # `088`. A loop that never runs reads exactly like one that works.
            batch_group_id=polygon.batch_group_id,
        )
        db.add(copy)
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
    admin: User = Depends(require("delivery.manage")),
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
        raise NotFoundError("Zone not found")
    if polygon.version.is_active:
        raise ConflictError(
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
        "alternate_providers": list(polygon.alternate_providers or []),
        "branch_id": str(polygon.branch_id) if polygon.branch_id else None,
        "batch_group_id": (
            str(polygon.batch_group_id) if polygon.batch_group_id else None
        ),
        "display_order": polygon.display_order,
    }

    if data.delivery_fee is not None:
        polygon.delivery_fee = data.delivery_fee
    if data.pricing_mode is not None:
        modes = {m.value for m in DeliveryPricingEnum}
        if data.pricing_mode not in modes:
            raise BadRequestError(
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
            raise BadRequestError(
                f"Unknown courier '{data.fulfilment_provider}'. "
                f"Choose one of: {', '.join(sorted(allowed))}",
            )
        polygon.fulfilment_provider = data.fulfilment_provider
        # A zone that changes courier leaves any run it was riding.
        #
        # "A run is one booking with one courier" — `assert_group_fits_polygon`
        # says so, and until now this could not arise: a draft never carried a
        # `batch_group_id`, because `create_version` dropped it. Now that the
        # copy keeps it, changing a draft zone's courier can leave it pointed at
        # a group that books somebody else, and nothing downstream would notice
        # until the window closed and the booking was refused — an hour after
        # anybody could have acted on it.
        #
        # Detached rather than refused, following the same reasoning the column
        # already gives for its `SET NULL`: a zone with no run dispatches
        # immediately, which costs a courier fare, and a zone pointed at a
        # schedule it cannot use never goes out at all. There is also no route
        # that attaches a zone to a group, so refusing here would make the
        # courier field permanently uneditable on every batched zone.
        #
        # It lands in the audit entry below, because `batch_group_id` is in both
        # the `before` and `to` dicts — so the one thing that must not be silent
        # is not.
        #
        # "Cannot use" is asked of `courier_service.may_be_carried_by` rather
        # than by comparing the two strings, so this and
        # `assert_group_fits_polygon` cannot come to different conclusions about
        # one pairing. Since `126` they are not the same question: a Slider zone
        # rides the Lalamove run it has always ridden, because every order in it
        # but the pilot account's is handed back to Lalamove automatically.
        if polygon.batch_group_id is not None:
            group = await db.get(DeliveryBatchGroup, polygon.batch_group_id)
            if group is not None and not courier_service.may_be_carried_by(
                polygon.fulfilment_provider, group.courier_code
            ):
                polygon.batch_group_id = None
    if data.alternate_providers is not None:
        allowed = {p.value for p in FulfilmentProviderEnum}
        unknown = [c for c in data.alternate_providers if c not in allowed]
        if unknown:
            raise BadRequestError(
                f"Unknown courier '{unknown[0]}'. "
                f"Choose from: {', '.join(sorted(allowed))}",
            )
        # Read against the value the zone is *ending up* with, not the one it
        # started with: a request that changes both at once would otherwise be
        # judged against a courier that is on its way out.
        preferred = data.fulfilment_provider or polygon.fulfilment_provider
        if preferred in data.alternate_providers:
            raise BadRequestError(
                f"'{preferred}' already carries this zone, so it cannot also be "
                "an alternate. Alternates are where an order goes when that "
                "courier will not take it.",
            )
        # Order-preserving, because the admin picks the order they want to be
        # offered and `dict.fromkeys` keeps it while `set` would not.
        polygon.alternate_providers = list(dict.fromkeys(data.alternate_providers))
    if data.branch_id is not None:
        branch = await db.get(Branch, data.branch_id)
        if branch is None or branch.deleted_at is not None:
            raise BadRequestError(
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
                # NOT NULL since 088 — every zone answers for itself, because
                # the national number it used to fall back to is gone.
                "free_delivery_threshold": float(polygon.free_delivery_threshold),
                "fulfilment_provider": polygon.fulfilment_provider,
                "alternate_providers": list(polygon.alternate_providers or []),
                "branch_id": str(polygon.branch_id) if polygon.branch_id else None,
                "batch_group_id": (
                    str(polygon.batch_group_id) if polygon.batch_group_id else None
                ),
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
    admin: User = Depends(require("delivery.manage")),
):
    """
    Make this map the live one. Also how a rollback is done.

    Only one map can be active — the database enforces it — so the outgoing one
    is stepped down in the same transaction.
    """
    version = await _load_version(db, version_id)
    if not version.polygons:
        raise BadRequestError(
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
    admin: User = Depends(require("delivery.manage")),
):
    """Throw away a draft. The live map cannot be deleted."""
    version = await _load_version(db, version_id)
    if version.is_active:
        raise ConflictError(
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
    _admin: User = Depends(require("delivery.manage")),
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
        "pickup_fee": float(settings.pickup_fee),
    }
