"""
Every request and response shape the delivery-map screens use.

Eleven of these were declared at the top of the old module and two —
`DeliverySettingsResponse` and `DeliverySettingsUpdate` — fourteen hundred
lines below, beside the two routes that return them. One home.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.courier import Courier
from app.models.delivery_batch import (
    DeliveryBatch,
    DeliveryBatchGroup,
    DeliveryBatchWindow,
)
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
