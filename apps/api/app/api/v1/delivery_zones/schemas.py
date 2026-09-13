"""
Every request and response shape the delivery-map screens use.

Eleven of these were declared at the top of the old module and two —
`DeliverySettingsResponse` and `DeliverySettingsUpdate` — fourteen hundred
lines below, beside the two routes that return them. One home.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.courier import Courier
from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
)
from app.models.delivery_settings import DeliverySettings


def _point_count(geometry: Any) -> int:
    if not isinstance(geometry, dict):
        return 0
    polys = (
        geometry.get("coordinates") or []
        if geometry.get("type") == "MultiPolygon"
        else [geometry.get("coordinates") or []]
    )
    return sum(len(ring) for poly in polys for ring in poly)


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
            display_order=p.display_order,
            point_count=_point_count(p.geometry),
        )


class PolygonPageVersion(BaseModel):
    """Just enough of the version for the table header: which map, and whether
    editing a row writes to the live storefront (it does, in place, when active)."""

    id: str
    name: str
    is_active: bool


class PolygonPage(BaseModel):
    """One page of a version's zones, for the admin table — the per-area map has
    ~97 of them, too many for the all-at-once shape `VersionResponse` returns."""

    items: list[PolygonResponse]
    total: int
    page: int
    per_page: int
    pages: int
    version: PolygonPageVersion | None


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
    #: Like every other field here, `null` (or an omitted key) means "leave this
    #: untouched". It used to mean "clear this zone's override and fall back to
    #: the national threshold", but that fallback is gone — the column is NOT
    #: NULL and every zone answers for itself — so there is nothing to clear back
    #: to, and writing the `null` through only 500'd on a NOT NULL column
    #: (F-COU-10). The handler tests `is not None`, not `model_fields_set`.
    free_delivery_threshold: Decimal | None = Field(None, ge=0)
    fulfilment_provider: str | None = None
    #: The couriers this zone's orders may be moved to. Replaces the list
    #: wholesale rather than merging — "these are the alternates" is one
    #: decision, and a merge would make removing the last one impossible.
    alternate_providers: list[str] | None = None
    branch_id: uuid.UUID | None = None
    display_order: int | None = None


class CourierResponse(BaseModel):
    """A carrier and what it promises, for the admin's Estimates screen."""

    code: str
    name: str
    unbatched_promise_kind: str
    unbatched_promise_minutes: int | None
    unbatched_promise_days: int
    is_active: bool
    #: Zones currently carried by this courier on the live map. A courier with
    #: none is one whose promise nobody is being quoted.
    zone_count: int

    #: True for a marketplace channel (Talabat, Noon Food, …) — couriers only in
    #: the sense of who carries the bag. A label, not a gate: a marketplace has no
    #: delivery promise MM quotes, and its fees are scraped from its own
    #: statement, never configured here.
    is_aggregator: bool = False

    @classmethod
    def of(cls, c: Courier, zone_count: int) -> "CourierResponse":
        return cls(
            code=c.code,
            name=c.name,
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
        )


class CourierUpdate(BaseModel):
    """
    What a courier promises.

    `code` is not here: it is the join key every polygon already holds, so it is
    the address of the row, not one of its editable fields.
    """

    #: `minutes` or `next_day`. Which of the two numbers below is read.
    unbatched_promise_kind: str | None = None
    #: Ready-to-door, for a courier we dispatch ourselves. A day either side of
    #: sensible is refused rather than quietly quoted.
    unbatched_promise_minutes: int | None = Field(None, ge=1, le=1440)
    #: Handover-to-door, for a courier that collects on its own schedule.
    unbatched_promise_days: int | None = Field(None, ge=1, le=30)
    is_active: bool | None = None


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
