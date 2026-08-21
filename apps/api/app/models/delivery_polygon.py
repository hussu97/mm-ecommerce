from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, utcnow

if TYPE_CHECKING:
    from .delivery_batch import DeliveryBatchGroup  # noqa: F401


class FulfilmentProviderEnum(str, enum.Enum):
    """Who carries an order out of the kitchen.

    Stored as plain text rather than a PostgreSQL enum: adding a courier should
    be an insert, not a migration that rewrites a type while the storefront is
    reading from it.
    """

    #: Booked over the Lalamove API — quoted, dispatched and tracked in code.
    LALAMOVE = "lalamove"
    #: Booked over noon's Rider-on-Demand API, sold as "noon Send". On a bike it
    #: is cheaper than Lalamove at every distance in range, but it cannot cross
    #: an emirate boundary and will not carry a run past 20 km, so from the
    #: Sharjah kitchen it only ever serves the inner part of Sharjah. Falls back
    #: to Lalamove whenever it will not take a job — including, deliberately, for
    #: every customer outside the trial while it runs against noon's staging.
    NOON_SEND = "noon_send"
    #: Whoever we already use. No integration: the same manual flow as today.
    THIRD_PARTY = "third_party"


#: What a zone may move an order to, when nobody has said otherwise.
#:
#: Keyed on the zone's preferred courier, because that is what decides which
#: alternates are even plausible:
#:
#: * **Lalamove** zones get third party and *not* noon Send. noon Send cannot
#:   cross an emirate boundary and will not carry a run past 20 km, so a zone we
#:   gave to Lalamove is a zone they probably cannot reach at all.
#: * **third party** zones get Lalamove — the escape that already existed, and
#:   the reason this whole thing was built.
#: * **noon Send** zones get both. They are inside Sharjah by construction, so
#:   Lalamove reaches them and a van reaches them.
#:
#: The same policy `courier_service._dispatch_once` already applies to automatic
#: dispatch, which falls noon Send back to Lalamove and never the reverse. This
#: is that rule made explicit, per zone, and editable in the console.
#:
#: Seeded into `delivery_polygons.alternate_providers` by migration
#: `125_zone_alternates`, which carries its own copy — a migration has to keep
#: describing the database as it was, so it cannot import this. The two are held
#: together by `tests/unit/test_zone_alternate_defaults.py`.
DEFAULT_ALTERNATES: dict[str, list[str]] = {
    FulfilmentProviderEnum.LALAMOVE.value: [FulfilmentProviderEnum.THIRD_PARTY.value],
    FulfilmentProviderEnum.THIRD_PARTY.value: [FulfilmentProviderEnum.LALAMOVE.value],
    FulfilmentProviderEnum.NOON_SEND.value: [
        FulfilmentProviderEnum.THIRD_PARTY.value,
        FulfilmentProviderEnum.LALAMOVE.value,
    ],
}


class DeliveryPricingEnum(str, enum.Enum):
    """Where a zone's delivery fee comes from.

    Two zones can be served by the same courier and still be priced completely
    differently. Around the kitchen the cost of a run is known well enough to
    publish a flat number and absorb the variance; an hour's drive away it is
    not, and quoting a flat fee there means either overcharging the near half of
    the zone or losing money on the far half.
    """

    #: The zone's own `delivery_fee`. Fixed, published, and independent of what
    #: any individual run turns out to cost.
    STATIC = "static"
    #: Whatever the courier quotes for this exact pin, rounded up to the whole
    #: dirham. A pin the courier will not quote cannot be delivered to at all.
    DYNAMIC = "dynamic"


class DeliveryPolygonVersion(Base, UUIDMixin):
    """
    One complete map of the delivery zones.

    Zones get redrawn — a courier stops covering an area, a fee changes, an
    emirate gets split. Editing rows in place makes that irreversible: if the
    new map prices something wrong, the old one is gone. A version owns the
    whole set, so publishing is one flag and rolling back is the same flag
    pointed at yesterday's map.
    """

    __tablename__ = "delivery_polygon_versions"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exactly one row may be true — enforced by a partial unique index.
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    polygons: Mapped[list[DeliveryPolygon]] = relationship(
        "DeliveryPolygon",
        back_populates="version",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<DeliveryPolygonVersion {self.name}{' (active)' if self.is_active else ''}>"


class DeliveryPolygon(Base, UUIDMixin):
    """
    A delivery zone and what it costs to reach.

    `geometry` is GeoJSON — a Polygon or MultiPolygon in [lng, lat] order, which
    is the order GeoJSON and Google both use. The bounding box is stored
    alongside so the common case (a point nowhere near this zone) is four
    comparisons instead of walking a few thousand vertices.
    """

    __tablename__ = "delivery_polygons"

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_polygon_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: Only read when `pricing_mode` is static. A dynamic zone keeps the column
    #: at zero rather than a plausible-looking number nothing charges, so a row
    #: cannot be misread as "we charge 50 here" by anyone glancing at the table.
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    #: Static is the safe default: a zone added without thinking about pricing
    #: charges its own published fee rather than silently handing the price over
    #: to a courier API.
    pricing_mode: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=DeliveryPricingEnum.STATIC.value,
        server_default=DeliveryPricingEnum.STATIC.value,
    )
    #: Whether a basket over the threshold delivers free here.
    #:
    #: A flag of its own rather than something inferred from the fee or the
    #: courier. It used to be read off `pricing_mode`, which worked only while
    #: the fixed-fee zones and the ones we deliver ourselves were the same set;
    #: the moment a third-party zone got a fixed fee too, that proxy started
    #: giving delivery away in the places it costs the most to reach. False by
    #: default, because a zone drawn without anyone thinking about the offer
    #: should not be making it.
    free_delivery_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The basket that earns free delivery *here*, when that is not the same
    #: number as everywhere else.
    #:
    #: One national threshold only works while every zone costs about the same to
    #: reach, and ours do not: a bike run inside Sharjah costs AED 13 and a car to
    #: Jebel Ali costs AED 59. A single number is therefore either withholding an
    #: offer that is cheap to honour, or funding one that is not — and it was
    #: doing both at once.
    #:
    #: Required. It was nullable, meaning "fall back to the national number",
    #: and the national number no longer exists — every zone answers for itself
    #: or the question has no answer at all. Zero is a real value and means free
    #: delivery at any basket, which is what Sharjah does.
    free_delivery_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="150.00"
    )
    #: The branch that serves this zone — bakes the order, hands it to the
    #: courier, and sees it on its register. Exactly one per zone: the shapes are
    #: disjoint, so a pin resolves to one zone and therefore one kitchen, with
    #: no tie-break to invent. A branch serves many zones.
    #:
    #: `RESTRICT` rather than `SET NULL`: a branch with live zones pointing at it
    #: is a branch that is taking orders, and deleting it should fail loudly
    #: rather than quietly orphan the map.
    #:
    #: Nullable, and null means "fall back to the single configured pickup
    #: branch" — which is how the column could land without changing behaviour.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Which courier serves this zone. Third party is the safe default: it means
    # "do what we have always done", so a zone added without thinking about
    # dispatch cannot start booking real couriers by accident.
    #
    # The *preferred* one. It is what every order placed here is priced and
    # dispatched against, and it is unchanged — the column below is what makes
    # the word "preferred" mean anything.
    fulfilment_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FulfilmentProviderEnum.THIRD_PARTY.value,
        server_default=FulfilmentProviderEnum.THIRD_PARTY.value,
    )
    #: The couriers an order in this zone may be *moved* to when the preferred
    #: one will not carry it.
    #:
    #: A courier declining a job is ordinary and a courier going quiet is not
    #: rare, and until this existed a stuck order had exactly one escape — third
    #: party onto Lalamove, in that direction only. Everything else needed a
    #: phone call. This is the same decision written down per zone: what may
    #: this order become, here.
    #:
    #: Emphatically not "every other courier". noon Send cannot cross an emirate
    #: boundary and will not take a run past 20 km, so offering a Dubai order to
    #: them produces a refusal at best and a booking nobody can service at worst
    #: — which is why a Lalamove zone lists third party and not noon Send. The
    #: seeded defaults say exactly what `courier_service` already does
    #: automatically; this makes the same policy true of the manual path, and
    #: editable without a deploy.
    #:
    #: JSONB rather than a join table because a map version is drafted by
    #: cloning its polygons wholesale, and an array clones with the row. The
    #: values are checked against `FulfilmentProviderEnum` in the zones router
    #: rather than by a native enum, for the reason given above it.
    #:
    #: Empty by default, which reads as "no alternates" and is the safe answer:
    #: a zone drawn without anyone thinking about this cannot have its orders
    #: moved anywhere.
    alternate_providers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    geometry: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    min_lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    max_lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    min_lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    max_lng: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)

    # Smaller, more specific zones should be tested before the big ones that
    # contain them, so an inner zone can override its surroundings.
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    version: Mapped[DeliveryPolygonVersion] = relationship(
        "DeliveryPolygonVersion", back_populates="polygons"
    )
    #: The run schedule this zone follows, or null for a zone that dispatches
    #: the moment an order is ready.
    #:
    #: A group rather than a schedule of its own, because "which orders share a
    #: courier booking" is a decision somebody makes, not something that should
    #: fall out of two zones happening to close a slot on the same minute — which
    #: is what it used to be. The three Dubai bands ride together because we said
    #: so; if one of their windows moves, the other two move with it, and no
    #: other zone is dragged along.
    #:
    #: `SET NULL`: deleting a group leaves its zones dispatching immediately,
    #: which is the safe reading. An order going out early costs a courier fare;
    #: an order pointed at a schedule that no longer exists never goes out.
    #:
    #: Only ever set to a group whose courier matches `fulfilment_provider` —
    #: enforced in `batching_service`, so the failure is a readable error rather
    #: than a constraint violation when a zone changes courier.
    batch_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("delivery_batch_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    batch_group: Mapped[DeliveryBatchGroup | None] = relationship(
        "DeliveryBatchGroup", back_populates="polygons"
    )

    def __repr__(self) -> str:
        price = (
            f"{self.delivery_fee}"
            if self.pricing_mode == DeliveryPricingEnum.STATIC.value
            else "dynamic"
        )
        return f"<DeliveryPolygon {self.name} @ {price} via {self.fulfilment_provider}>"
