"""Read the cached customer delivery locations as compact operational map cells."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import search as search_text
from app.core.phone import normalise_phone
from app.models.customer_cache import CustomerCache, CustomerDeliveryAreaCache
from app.models.delivery_polygon import DeliveryPolygon
from app.services.delivery import delivery_zone_service

# About two kilometres north/south in the UAE. This deliberately aggregates
# addresses before they leave the API: the admin can see demand, not a trail of
# individual homes.
_CELL_SIZE = Decimal("0.018")
_LAT_ORIGIN = Decimal("22.5")
_LNG_ORIGIN = Decimal("51.3")


@dataclass
class _Metrics:
    customer_ids: set[object] = field(default_factory=set)
    order_count: int = 0
    revenue: Decimal = Decimal("0")
    channels: dict[str, "_Metrics"] = field(default_factory=dict)


@dataclass
class _Cell(_Metrics):
    latitude: Decimal = Decimal("0")
    longitude: Decimal = Decimal("0")


def _add(
    metrics: _Metrics, point: CustomerDeliveryAreaCache, customer: CustomerCache
) -> None:
    """Accumulate an order into an aggregate and its source-channel split."""
    metrics.customer_ids.add(customer.id)
    metrics.order_count += 1
    metrics.revenue += Decimal(point.order_value)
    channel = str(point.source_channel or "other")
    by_channel = metrics.channels.setdefault(channel, _Metrics())
    by_channel.customer_ids.add(customer.id)
    by_channel.order_count += 1
    by_channel.revenue += Decimal(point.order_value)


def _metrics_payload(metrics: _Metrics) -> dict[str, object]:
    def payload(value: _Metrics) -> dict[str, float | int]:
        return {
            "customer_count": len(value.customer_ids),
            "order_count": value.order_count,
            "revenue": float(value.revenue),
            "aov": float(value.revenue / value.order_count)
            if value.order_count
            else 0.0,
        }

    return {
        **payload(metrics),
        "channel_breakdown": {
            channel: payload(value)
            for channel, value in sorted(metrics.channels.items())
        },
    }


def _ring_contains(ring: list[Any], longitude: float, latitude: float) -> bool:
    """Ray-cast one GeoJSON ring, treating malformed points as absent."""
    points = [
        (float(point[0]), float(point[1]))
        for point in ring
        if isinstance(point, list)
        and len(point) >= 2
        and isinstance(point[0], (int, float))
        and isinstance(point[1], (int, float))
    ]
    if len(points) < 3:
        return False
    inside = False
    previous = points[-1]
    for current in points:
        (x1, y1), (x2, y2) = previous, current
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses and longitude < (x2 - x1) * (latitude - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def _geometry_contains(
    geometry: dict[str, Any], longitude: float, latitude: float
) -> bool:
    """Whether a point belongs to a Polygon or MultiPolygon, excluding holes."""
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    if not isinstance(polygons, list):
        return False
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            continue
        outer, *holes = polygon
        if (
            isinstance(outer, list)
            and _ring_contains(outer, longitude, latitude)
            and not any(
                isinstance(hole, list) and _ring_contains(hole, longitude, latitude)
                for hole in holes
            )
        ):
            return True
    return False


async def load(
    db: AsyncSession,
    *,
    search: str | None,
    start: datetime | None,
    end: datetime | None,
) -> dict:
    """Return active geometry and matching cache rows grouped into heat cells."""
    statement = select(CustomerDeliveryAreaCache, CustomerCache).join(
        CustomerCache,
        CustomerCache.id == CustomerDeliveryAreaCache.customer_id,
    )
    if start is not None and end is not None:
        statement = statement.where(
            CustomerDeliveryAreaCache.order_created_at >= start,
            CustomerDeliveryAreaCache.order_created_at <= end,
        )
    if search:
        phone_search = normalise_phone(search) or search
        statement = statement.where(
            or_(
                search_text.contains(CustomerCache.name, search),
                search_text.contains(CustomerCache.email, search),
                search_text.contains(CustomerCache.phone, phone_search),
            )
        )
    rows = (await db.execute(statement)).all()

    active = await delivery_zone_service.get_active_version(db)
    zones: list[dict[str, Any]] = []
    if active is not None:
        polygons = (
            await db.scalars(
                select(DeliveryPolygon)
                .where(DeliveryPolygon.version_id == active.id)
                .order_by(DeliveryPolygon.display_order)
            )
        ).all()
        zones = [
            {
                "id": str(polygon.id),
                "name": polygon.name,
                "fulfilment_provider": polygon.fulfilment_provider,
                "geometry": polygon.geometry,
            }
            for polygon in polygons
        ]
    zone_metrics = {zone["id"]: _Metrics() for zone in zones}

    cells: dict[tuple[int, int], _Cell] = {}
    total_revenue = Decimal("0")
    customer_ids: set[object] = set()
    sources: Counter[str] = Counter()
    for point, customer in rows:
        lat_bucket = int((Decimal(point.latitude) - _LAT_ORIGIN) / _CELL_SIZE)
        lng_bucket = int((Decimal(point.longitude) - _LNG_ORIGIN) / _CELL_SIZE)
        key = (lat_bucket, lng_bucket)
        cell = cells.setdefault(
            key,
            _Cell(
                latitude=_LAT_ORIGIN
                + (Decimal(lat_bucket) + Decimal("0.5")) * _CELL_SIZE,
                longitude=_LNG_ORIGIN
                + (Decimal(lng_bucket) + Decimal("0.5")) * _CELL_SIZE,
            ),
        )
        _add(cell, point, customer)
        customer_ids.add(customer.id)
        total_revenue += Decimal(point.order_value)
        sources[point.source_channel] += 1
        for zone in zones:
            if _geometry_contains(
                zone["geometry"], float(point.longitude), float(point.latitude)
            ):
                _add(zone_metrics[zone["id"]], point, customer)

    ordered_cells = sorted(
        (
            {
                "latitude": float(cell.latitude),
                "longitude": float(cell.longitude),
                **_metrics_payload(cell),
            }
            for cell in cells.values()
        ),
        key=lambda cell: (-cell["customer_count"], cell["latitude"], cell["longitude"]),
    )
    order_count = len(rows)
    return {
        "version_name": active.name if active is not None else None,
        "zones": [
            {**zone, **_metrics_payload(zone_metrics[zone["id"]])} for zone in zones
        ],
        "cells": ordered_cells,
        "customer_count": len(customer_ids),
        "order_count": order_count,
        "revenue": float(total_revenue),
        "aov": float(total_revenue / order_count) if order_count else 0.0,
        "source_counts": dict(sorted(sources.items())),
    }
