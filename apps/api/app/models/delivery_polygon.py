from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

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
    # Kept so an order can still record which emirate it went to, and so the
    # admin list reads in familiar terms, but it no longer decides the price.
    region_slug: Mapped[str | None] = mapped_column(String(30), nullable=True)
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
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

    def __repr__(self) -> str:
        return f"<DeliveryPolygon {self.name} @ {self.delivery_fee}>"
