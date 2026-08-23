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

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.permissions import require
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.branch import Branch
from app.models.courier import Courier, UnbatchedPromiseEnum
from app.models.delivery_batch import (
    BatchStatusEnum,
    DeliveryBatch,
    DeliveryBatchGroup,
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
    courier_service,
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
    free_delivery_threshold: float
    fulfilment_provider: str
    #: Where an order in this zone may be moved when the preferred courier will
    #: not carry it. See `DeliveryPolygon.alternate_providers`.
    alternate_providers: list[str]
    #: The kitchen that bakes this zone's orders and hands them to the courier.
    #: Null falls back to the single configured pickup branch.
    branch_id: str | None
    #: The run this zone travels on, or null for one that leaves alone.
    #:
    #: Read-only here — no route attaches a zone to a group, only migrations do.
    #: It is exposed because changing a zone's courier *detaches* it (a run is
    #: one booking with one courier), and a setting that can be lost by editing
    #: a neighbouring field has to be visible while somebody edits it.
    batch_group_id: str | None
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
            free_delivery_threshold=float(p.free_delivery_threshold),
            fulfilment_provider=p.fulfilment_provider,
            alternate_providers=list(p.alternate_providers or []),
            branch_id=str(p.branch_id) if p.branch_id else None,
            batch_group_id=str(p.batch_group_id) if p.batch_group_id else None,
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
    #: The couriers this zone's orders may be moved to. Replaces the list
    #: wholesale rather than merging — "these are the alternates" is one
    #: decision, and a merge would make removing the last one impossible.
    alternate_providers: list[str] | None = None
    branch_id: uuid.UUID | None = None
    display_order: int | None = None


class BatchWindowResponse(BaseModel):
    id: str
    group_id: str
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
            group_id=str(w.group_id),
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


class BatchGroupResponse(BaseModel):
    """
    A set of zones whose orders ride together, and the schedule they share.

    Everything the batching screen needs in one row, because the question it
    answers — "which zones leave together, on whose van, and how long after"
    — used to require reading five zones' schedules side by side and noticing
    that their end times matched.
    """

    id: str
    name: str
    courier_code: str
    #: How long after the run leaves that the last drop is through a door. Per
    #: group, because it is a property of the route: 90 for Dubai, 120 for the
    #: northern zones on the same rate card.
    delivery_minutes_after_dispatch: int
    is_active: bool
    #: The zones on this schedule, in map order.
    zone_names: list[str]
    windows: list[BatchWindowResponse]

    @classmethod
    def of(
        cls,
        g: DeliveryBatchGroup,
        zone_names: list[str],
        windows: list[DeliveryBatchWindow],
    ) -> "BatchGroupResponse":
        return cls(
            id=str(g.id),
            name=g.name,
            courier_code=g.courier_code,
            delivery_minutes_after_dispatch=g.delivery_minutes_after_dispatch,
            is_active=g.is_active,
            zone_names=zone_names,
            windows=[BatchWindowResponse.of(w) for w in windows],
        )


class BatchGroupUpdate(BaseModel):
    """
    The parts of a schedule that are a commercial decision rather than a
    structural one.

    `name` and `courier_code` are deliberately absent. Which carrier a group
    books is the thing `supports_batching` guards and the thing its zones were
    assigned against; moving it is a re-partitioning of the map, not a number
    somebody adjusts on a Tuesday.
    """

    delivery_minutes_after_dispatch: int | None = Field(None, ge=1, le=1440)
    is_active: bool | None = None


class CourierResponse(BaseModel):
    """
    A carrier and what it promises, for the admin's Estimates screen.

    `supports_batching` is read-only here and shown anyway: it is the reason a
    courier does or does not appear on the batching screen, and an admin
    wondering why noon Send has no schedule deserves the answer on the same
    page as the numbers.
    """

    code: str
    name: str
    supports_batching: bool
    unbatched_promise_kind: str
    unbatched_promise_minutes: int | None
    unbatched_promise_days: int
    is_active: bool
    #: Zones currently carried by this courier on the live map. A courier with
    #: none is one whose promise nobody is being quoted.
    zone_count: int

    #: True for a marketplace channel (Talabat, Noon Food, …). The screen splits
    #: on it: a marketplace has no promise to configure and nothing but rates,
    #: and a courier we dispatch has a promise and no rates at all.
    is_aggregator: bool = False

    #: What a marketplace takes off an order, as **percentages** (`25.00` is
    #: 25%), quoted before VAT the way the contracts are written and the
    #: invoices arrive.
    #:
    #: Null is a real answer and not a missing one: it means nobody has supplied
    #: the rate yet, and it leaves those orders' fees — and therefore their net
    #: — unknown rather than pretending they were free. Only Noon Food's are
    #: agreed today. Always null on a courier MM dispatches, which is billed per
    #: booking on the order's delivery record instead.
    #: Each fee is a **pair** — a share of the basket plus a flat amount, both
    #: before VAT — because that is how the contracts are written ("25% plus two
    #: dirhams an order"). A fee is unknown only when both halves are null.
    commission_percent: Decimal | None = None
    commission_fixed: Decimal | None = None
    payment_fee_percent: Decimal | None = None
    payment_fee_fixed: Decimal | None = None

    @classmethod
    def of(cls, c: Courier, zone_count: int) -> "CourierResponse":
        return cls(
            code=c.code,
            name=c.name,
            supports_batching=c.supports_batching,
            unbatched_promise_kind=c.unbatched_promise_kind,
            unbatched_promise_minutes=c.unbatched_promise_minutes,
            unbatched_promise_days=c.unbatched_promise_days,
            is_active=c.is_active,
            zone_count=zone_count,
            # `bool(...)` rather than the attribute: the column is NOT NULL
            # with a server default, so a row added to the session but not yet
            # flushed still reads `None` here — which is every row a test builds
            # by hand, and every row this endpoint sees the instant after one is
            # created. Absent means "not a marketplace".
            is_aggregator=bool(c.is_aggregator),
            commission_percent=c.commission_percent,
            commission_fixed=c.commission_fixed,
            payment_fee_percent=c.payment_fee_percent,
            payment_fee_fixed=c.payment_fee_fixed,
        )


class CourierUpdate(BaseModel):
    """
    What a courier promises when there is no batch to wait for.

    `code` and `supports_batching` are not here. The code is the join key every
    polygon and every group already holds, and whether a carrier can carry
    several of our orders in one booking is a fact about their product, not a
    setting — turning it on for a courier that cannot would let a schedule be
    attached to a promise nothing can keep.
    """

    #: `minutes` or `next_day`. Which of the two numbers below is read.
    unbatched_promise_kind: str | None = None
    #: Ready-to-door, for a courier we dispatch ourselves. A day either side of
    #: sensible is refused rather than quietly quoted.
    unbatched_promise_minutes: int | None = Field(None, ge=1, le=1440)
    #: Handover-to-door, for a courier that collects on its own schedule.
    unbatched_promise_days: int | None = Field(None, ge=1, le=30)
    is_active: bool | None = None

    #: A marketplace's rates, as percentages before VAT. Refused on a courier MM
    #: dispatches — see `_assert_rates_belong_here`.
    #:
    #: 100 is the ceiling because a commission above it is somebody entering a
    #: rate they meant as something else, and it would post a negative net on
    #: every order that channel took until a human noticed.
    commission_percent: Decimal | None = Field(None, ge=0, le=100)
    payment_fee_percent: Decimal | None = Field(None, ge=0, le=100)
    #: The flat half of each pair, in the order currency and before VAT. No
    #: upper bound that would mean anything — a large flat fee is a strange
    #: contract, not an impossible one — but it cannot be negative, which would
    #: be a rebate wearing a fee's name.
    commission_fixed: Decimal | None = Field(None, ge=0)
    payment_fee_fixed: Decimal | None = Field(None, ge=0)


class BatchResponse(BaseModel):
    id: str
    #: The group whose schedule opened this run. Zones ride together because
    #: somebody put them in one group, so the group — not a zone — is what a run
    #: belongs to. `zone_name` is the honest answer to what is on it.
    group_id: str
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
            group_id=str(b.group_id),
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


# ── Batching ──────────────────────────────────────────────────────────────────


async def _load_polygon(db: AsyncSession, polygon_id: uuid.UUID) -> DeliveryPolygon:
    polygon = await db.get(DeliveryPolygon, polygon_id)
    if polygon is None:
        raise NotFoundError("Zone not found")
    return polygon


async def _load_group(db: AsyncSession, group_id: uuid.UUID) -> DeliveryBatchGroup:
    group = await db.get(DeliveryBatchGroup, group_id)
    if group is None:
        raise NotFoundError("Batch group not found")
    return group


async def _windows_of(
    db: AsyncSession, group_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    result = await db.execute(
        select(DeliveryBatchWindow)
        .where(DeliveryBatchWindow.group_id == group_id)
        .order_by(DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute)
    )
    return list(result.scalars().all())


def _reject_overlaps(windows: list[DeliveryBatchWindow]) -> None:
    clash = batching_service.overlapping(windows)
    if clash is None:
        return
    first, second = clash
    raise ConflictError(
        f'"{first.label}" and "{second.label}" both cover the same time. '
        "Two batches claiming one minute makes it a coin toss which run an "
        "order joins.",
    )


@router.get("/batch-groups", response_model=list[BatchGroupResponse])
async def list_batch_groups(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """
    Every schedule, with the zones on it.

    Declared beside `/{polygon_id}` routes, so the literal path has to be
    matched before anything that would read "batch-groups" as an id.
    """
    groups = (
        (await db.execute(select(DeliveryBatchGroup).order_by(DeliveryBatchGroup.name)))
        .scalars()
        .all()
    )
    out: list[BatchGroupResponse] = []
    for group in groups:
        zones = (
            (
                await db.execute(
                    select(DeliveryPolygon.name)
                    .where(DeliveryPolygon.batch_group_id == group.id)
                    .order_by(DeliveryPolygon.display_order)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            BatchGroupResponse.of(group, list(zones), await _windows_of(db, group.id))
        )
    return out


@router.put("/batch-groups/{group_id}", response_model=BatchGroupResponse)
async def update_batch_group(
    group_id: uuid.UUID,
    data: BatchGroupUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Change how long after a run leaves that its last box is through a door.

    The other half of what the checkout quotes a batched zone, and until now the
    half that needed a deploy: the window said when the van goes, and this says
    how long it then takes. Unlike a fee it takes effect immediately and is not
    versioned — a wrong number here delays nothing and overcharges nobody, it
    just says the wrong time, and the fix is to say the right one.

    Orders already quoted are untouched. What the shop said out loud is a
    record, not a derivation (`order_deliveries` keeps it), so moving this
    number moves the next promise rather than rewriting the last one.
    """
    group = await _load_group(db, group_id)
    before = {
        "delivery_minutes_after_dispatch": group.delivery_minutes_after_dispatch,
        "is_active": group.is_active,
    }
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(group, field, value)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch_group",
        entity_id=str(group.id),
        entity_label=group.name,
        admin=admin,
        changes={
            "from": before,
            "to": {
                "delivery_minutes_after_dispatch": (
                    group.delivery_minutes_after_dispatch
                ),
                "is_active": group.is_active,
            },
        },
        request=request,
    )

    zones = (
        (
            await db.execute(
                select(DeliveryPolygon.name)
                .where(DeliveryPolygon.batch_group_id == group.id)
                .order_by(DeliveryPolygon.display_order)
            )
        )
        .scalars()
        .all()
    )
    return BatchGroupResponse.of(group, list(zones), await _windows_of(db, group.id))


# ── Couriers ──────────────────────────────────────────────────────────────────
#
# The unbatched half of the delivery promise. A zone in no batch group — every
# noon Send zone, and every third-party one — is quoted straight from these two
# numbers, and until now neither had a way in that was not a migration.


async def _live_zone_counts(db: AsyncSession) -> dict[str, int]:
    """How many zones on the published map each courier currently carries."""
    version = await delivery_zone_service.get_active_version(db)
    if version is None:
        return {}
    rows = await db.execute(
        select(DeliveryPolygon.fulfilment_provider, func.count())
        .where(DeliveryPolygon.version_id == version.id)
        .group_by(DeliveryPolygon.fulfilment_provider)
    )
    return {provider: int(count) for provider, count in rows.all()}


@router.get("/couriers", response_model=list[CourierResponse])
async def list_couriers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """Every carrier and what it promises."""
    couriers = (
        (await db.execute(select(Courier).order_by(Courier.name))).scalars().all()
    )
    counts = await _live_zone_counts(db)
    return [CourierResponse.of(c, counts.get(c.code, 0)) for c in couriers]


@router.put("/couriers/{code}", response_model=CourierResponse)
async def update_courier(
    code: str,
    data: CourierUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Change what a courier promises.

    Refuses a `kind` of `minutes` with no minutes to quote, in either the body
    or the row it would leave behind. That combination is the one way to make
    the resolver fall back to its own literal — a number nobody chose, quoted
    to a customer as though somebody had.
    """
    courier = (
        await db.execute(select(Courier).where(Courier.code == code))
    ).scalar_one_or_none()
    if courier is None:
        raise NotFoundError(f"Courier '{code}' not found")

    kind = data.unbatched_promise_kind or courier.unbatched_promise_kind
    allowed = {member.value for member in UnbatchedPromiseEnum}
    if kind not in allowed:
        raise BadRequestError(
            f"Unknown promise kind '{kind}'. Allowed: {sorted(allowed)}"
        )
    minutes = (
        data.unbatched_promise_minutes
        if data.unbatched_promise_minutes is not None
        else courier.unbatched_promise_minutes
    )
    if kind == UnbatchedPromiseEnum.MINUTES.value and not minutes:
        raise BadRequestError(
            f"{courier.name} promises an hour rather than a day, so it needs a "
            "number of minutes. Set one, or switch it to next-day."
        )

    _assert_rates_belong_here(courier, data)

    before = CourierResponse.of(courier, 0).model_dump(exclude={"zone_count"})
    # `exclude_unset`, not `exclude_none`. A rate has three states — a number,
    # zero, and "nobody has told us" — and under `exclude_none` the third was
    # unreachable: having once typed 25 into Talabat by mistake, there was no
    # way back to unknown, only to a zero that claims the channel is free. What
    # the client did not send is still left alone, which is all `exclude_none`
    # was ever there for.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(courier, field, value)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="courier",
        entity_id=courier.code,
        entity_label=courier.name,
        admin=admin,
        changes={
            "from": before,
            "to": CourierResponse.of(courier, 0).model_dump(exclude={"zone_count"}),
        },
        request=request,
    )
    counts = await _live_zone_counts(db)
    return CourierResponse.of(courier, counts.get(courier.code, 0))


def _assert_rates_belong_here(courier: Courier, data: "CourierUpdate") -> None:
    """
    Refuse a commission on a courier MM dispatches itself.

    Those are billed per booking, and the amount lands on
    `order_deliveries.cost_total`, which `order_economics` already subtracts. A
    percentage here as well would take the same cost off the same order twice —
    and the resulting margin would be wrong in the direction nobody checks,
    because a figure that looks worse than expected gets believed.
    """
    if courier.is_aggregator:
        return
    sent = data.model_dump(exclude_unset=True)
    offending = [
        field
        for field in (
            "commission_percent",
            "commission_fixed",
            "payment_fee_percent",
            "payment_fee_fixed",
        )
        if sent.get(field) is not None
    ]
    if offending:
        raise BadRequestError(
            f"{courier.name} is a courier MM dispatches, not a marketplace. "
            "What it charges is recorded per booking on the order's delivery "
            "record; a percentage here would subtract that cost a second time."
        )


@router.get(
    "/batch-groups/{group_id}/batch-windows", response_model=list[BatchWindowResponse]
)
async def list_batch_windows(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """When orders in this group travel together. All times are Dubai time."""
    await _load_group(db, group_id)
    return [BatchWindowResponse.of(w) for w in await _windows_of(db, group_id)]


@router.post(
    "/batch-groups/{group_id}/batch-windows",
    response_model=BatchWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_window(
    group_id: uuid.UUID,
    data: BatchWindowWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Add a slot to a group's schedule.

    Only a courier that can carry several of our orders in one booking may have
    one — `supports_batching` on the courier row. A schedule on anything else is
    a setting that does nothing, which is worse than an absent one because
    somebody will come to rely on it.
    """
    group = await _load_group(db, group_id)
    courier = (
        await db.execute(select(Courier).where(Courier.code == group.courier_code))
    ).scalar_one_or_none()
    if courier is None or not courier.supports_batching:
        raise BadRequestError(
            f"'{group.name}' books {group.courier_code}, which cannot carry "
            "several of our orders in one booking — so it has no run to share.",
        )

    window = DeliveryBatchWindow(group_id=group_id, **data.model_dump())
    _reject_overlaps([*await _windows_of(db, group_id), window])
    db.add(window)
    await db.flush()

    # Anything already waiting is re-derived, so adding a slot picks up the
    # orders that fell into the gap it just filled.
    await batching_service.reschedule_group(db, group_id)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=f"{group.name} · {window.label}",
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
    admin: User = Depends(require("delivery.manage")),
):
    """
    Move a slot, and move everything still waiting on it.

    An order whose new window has already closed goes out on its own rather
    than waiting until tomorrow for a slot that has been and gone.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise NotFoundError("Batch window not found")

    before = BatchWindowResponse.of(window).model_dump()
    for field, value in data.model_dump().items():
        setattr(window, field, value)
    _reject_overlaps(await _windows_of(db, window.group_id))
    await db.flush()

    moved = await batching_service.reschedule_group(db, window.group_id)
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
    admin: User = Depends(require("delivery.manage")),
):
    """
    Remove a slot. Orders waiting on it are re-derived, and any that no longer
    fall in a window go out on their own rather than being stranded.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise NotFoundError("Batch window not found")

    group_id = window.group_id
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
    await batching_service.reschedule_group(db, group_id)


@router.get("/batches", response_model=list[BatchResponse])
async def list_batches(
    status_filter: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
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

    group_names = dict(
        (
            await db.execute(
                select(DeliveryBatchGroup.id, DeliveryBatchGroup.name).where(
                    DeliveryBatchGroup.id.in_({b.group_id for b in batches})
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
            # What is actually on the run. Falls back to the group that opened
            # it for a batch that has not collected anything yet.
            ", ".join(on_run.get(b.id) or []) or group_names.get(b.group_id),
            numbers.get(b.id, []),
        )
        for b in batches
    ]


@router.post("/batches/{batch_id}/dispatch", response_model=BatchResponse)
async def dispatch_batch_now(
    batch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
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
        raise NotFoundError("Batch not found")
    if batch.status == BatchStatusEnum.DISPATCHED.value and not batch.next_attempt_at:
        raise ConflictError("This run has already gone out.")

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
    group = await db.get(DeliveryBatchGroup, batch.group_id)
    return BatchResponse.of(batch, group.name if group else None, [])


# ── Settings ──────────────────────────────────────────────────────────────────


class DeliverySettingsResponse(BaseModel):
    """
    The delivery numbers with no zone to belong to.

    `free_delivery_threshold` and `default_delivery_fee` used to be here and are
    gone. Every polygon carries its own threshold, so a national one was a second
    answer to a question the map already settles — and the admin was printing it
    under "applies to every zone" while no zone read it. A pin outside every
    polygon is now unserviceable rather than charged a default.
    """

    id: str
    pickup_fee: float
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
            pickup_fee=float(s.pickup_fee),
            low_order_fee=float(s.low_order_fee or 0),
            low_order_threshold=(
                None if s.low_order_threshold is None else float(s.low_order_threshold)
            ),
        )


class DeliverySettingsUpdate(BaseModel):
    pickup_fee: Decimal | None = Field(None, ge=0)
    low_order_fee: Decimal | None = Field(None, ge=0)
    #: Null is a real instruction here — "switch the fee off" — so unlike the
    #: fields above, omitted and null are not the same. The handler reads
    #: `model_fields_set` to tell them apart.
    low_order_threshold: Decimal | None = Field(None, ge=0)


@router.get("/settings", response_model=DeliverySettingsResponse)
async def get_delivery_settings(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
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
    admin: User = Depends(require("delivery.manage")),
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
