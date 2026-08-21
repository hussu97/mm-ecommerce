from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
    DeliveryPricingEnum,
    FulfilmentProviderEnum,
)

__all__ = [
    "Zone",
    "find_zone",
    "get_active_version",
    "get_active_zones",
    "invalidate_cache",
    "point_in_geometry",
]


@dataclass(frozen=True)
class Zone:
    """A delivery polygon flattened into the shape the lookup actually needs."""

    id: uuid.UUID
    name: str
    delivery_fee: Decimal
    fulfilment_provider: str
    min_lat: float
    max_lat: float
    min_lng: float
    max_lng: float
    rings: tuple[tuple[tuple[tuple[float, float], ...], ...], ...]
    #: Defaulted so a zone built by hand — in a test, or from a row written
    #: before the column existed — charges its own fee rather than reaching for
    #: a courier that may not answer.
    pricing_mode: str = DeliveryPricingEnum.STATIC.value
    #: Whether a qualifying basket delivers free here. Defaulted off so a zone
    #: built by hand cannot give delivery away by omission.
    free_delivery_eligible: bool = False
    #: The kitchen that serves this zone. None falls back to the single
    #: configured pickup branch, which is what every zone did before zones knew
    #: about branches at all — so it defaults, and a map drawn before the column
    #: existed behaves exactly as it did.
    branch_id: uuid.UUID | None = None
    #: The basket that earns free delivery here. Required on the row; defaulted
    #: here only so a zone built by hand in a test does not have to name one.
    free_delivery_threshold: Decimal | None = None
    #: The run schedule this zone follows, or None for a zone that dispatches the
    #: moment an order is ready. Read by `delivery_promise` — a zone in a group
    #: is promised its group's next window close, a zone without one is promised
    #: its courier's own answer.
    batch_group_id: uuid.UUID | None = None
    #: The couriers an order here may be *moved* to when `fulfilment_provider`
    #: will not carry it. Read by `fulfilment_reassignment`, and only on the
    #: fallback path — an order normally answers this from the polygon its own
    #: `polygon_id` points at, which is the map it was actually priced against.
    #:
    #: Defaults to empty, so a zone built by hand in a test offers no
    #: reassignment rather than silently offering every courier.
    alternate_providers: tuple[str, ...] = ()

    @property
    def is_lalamove(self) -> bool:
        return self.fulfilment_provider == FulfilmentProviderEnum.LALAMOVE.value

    @property
    def is_noon_send(self) -> bool:
        return self.fulfilment_provider == FulfilmentProviderEnum.NOON_SEND.value

    @property
    def books_itself(self) -> bool:
        """A zone we dispatch over an API rather than by hand.

        Wider than `is_batched`: noon Send is ours to dispatch but never shares a
        run, so the two questions stopped having the same answer the moment a
        second courier arrived. "When will it arrive" turns on this one — we know
        the schedule for anything we dispatch — while "does it wait for a van"
        turns on the other.
        """
        return self.fulfilment_provider != FulfilmentProviderEnum.THIRD_PARTY.value

    @property
    def is_batched(self) -> bool:
        """Whether orders here wait for a shared run rather than going alone.

        A property of the zone's **group membership**, not of its courier. It
        used to return `is_lalamove`, which was true of the courier and only
        accidentally true of the zones: a Lalamove zone with no schedule went
        out immediately and still answered "batched" here. Now a zone is batched
        exactly when somebody put it in a group.
        """
        return self.batch_group_id is not None

    @property
    def is_dynamic(self) -> bool:
        """Priced from the courier's own quote rather than from `delivery_fee`."""
        return self.pricing_mode == DeliveryPricingEnum.DYNAMIC.value

    def contains(self, lat: float, lng: float) -> bool:
        # Four comparisons reject almost every zone before any real work.
        if not (
            self.min_lat <= lat <= self.max_lat and self.min_lng <= lng <= self.max_lng
        ):
            return False
        return any(_point_in_polygon(lat, lng, polygon) for polygon in self.rings)


def _ray_crosses(lat: float, lng: float, ring: Sequence[tuple[float, float]]) -> bool:
    """
    Even-odd ray casting. Ring coordinates are GeoJSON order — (lng, lat).

    Counts how many times a ray cast east from the point crosses the ring. Odd
    means inside. Points exactly on an edge are not worth special-casing: a
    delivery boundary is not accurate to the millimetre anyway.
    """
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        lng_i, lat_i = ring[i]
        lng_j, lat_j = ring[j]
        if (lat_i > lat) != (lat_j > lat):
            x = (lng_j - lng_i) * (lat - lat_i) / (lat_j - lat_i) + lng_i
            if lng < x:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(
    lat: float, lng: float, polygon: Sequence[Sequence[tuple[float, float]]]
) -> bool:
    """First ring is the outline; any that follow are holes cut out of it."""
    if not polygon:
        return False
    if not _ray_crosses(lat, lng, polygon[0]):
        return False
    return not any(_ray_crosses(lat, lng, hole) for hole in polygon[1:])


def point_in_geometry(lat: float, lng: float, geometry: dict[str, Any]) -> bool:
    """Public helper for a raw GeoJSON Polygon / MultiPolygon."""
    polys = (
        geometry["coordinates"]
        if geometry.get("type") == "MultiPolygon"
        else [geometry.get("coordinates", [])]
    )
    return any(_point_in_polygon(lat, lng, poly) for poly in polys)


# The active map changes only when someone publishes one, so it is held in
# process rather than re-read and re-parsed on every quote. Keyed by version id
# so a rollback swaps the entry instead of serving a stale map.
_cache: dict[uuid.UUID, tuple[Zone, ...]] = {}


def invalidate_cache() -> None:
    _cache.clear()


async def get_active_version(db: AsyncSession) -> DeliveryPolygonVersion | None:
    result = await db.execute(
        select(DeliveryPolygonVersion).where(DeliveryPolygonVersion.is_active.is_(True))
    )
    return result.scalars().first()


async def get_active_zones(db: AsyncSession) -> tuple[Zone, ...]:
    version = await get_active_version(db)
    if version is None:
        return ()

    cached = _cache.get(version.id)
    if cached is not None:
        return cached

    result = await db.execute(
        select(DeliveryPolygon)
        .where(DeliveryPolygon.version_id == version.id)
        .order_by(DeliveryPolygon.display_order)
    )
    zones = tuple(_to_zone(p) for p in result.scalars().all())
    _cache[version.id] = zones
    return zones


def _to_zone(p: DeliveryPolygon) -> Zone:
    geometry = p.geometry or {}
    polys = (
        geometry.get("coordinates", [])
        if geometry.get("type") == "MultiPolygon"
        else [geometry.get("coordinates", [])]
    )
    rings = tuple(
        tuple(tuple((float(c[0]), float(c[1])) for c in ring) for ring in poly)
        for poly in polys
    )
    return Zone(
        id=p.id,
        name=p.name,
        delivery_fee=Decimal(str(p.delivery_fee)),
        fulfilment_provider=(
            p.fulfilment_provider or FulfilmentProviderEnum.THIRD_PARTY.value
        ),
        pricing_mode=(p.pricing_mode or DeliveryPricingEnum.STATIC.value),
        free_delivery_eligible=bool(p.free_delivery_eligible),
        batch_group_id=p.batch_group_id,
        free_delivery_threshold=(
            None
            if p.free_delivery_threshold is None
            else Decimal(str(p.free_delivery_threshold))
        ),
        branch_id=p.branch_id,
        alternate_providers=tuple(p.alternate_providers or ()),
        min_lat=float(p.min_lat),
        max_lat=float(p.max_lat),
        min_lng=float(p.min_lng),
        max_lng=float(p.max_lng),
        rings=rings,
    )


async def find_zone(db: AsyncSession, lat: float, lng: float) -> Zone | None:
    """
    The first zone on the active map containing the point.

    Order matters: zones are tested by `display_order`, so a small zone listed
    ahead of the large one it sits inside wins.
    """
    for zone in await get_active_zones(db):
        if zone.contains(lat, lng):
            return zone
    return None
