"""The read-optimised, cross-channel customer directory.

``orders`` remains the sales ledger and ``users`` remains the account ledger.
This pair is deliberately a cache: it holds the costly identity graph and the
customer-facing roll-up, never a second source of truth for an order.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class CustomerCacheState(Base):
    """One dirty bit set by source-table triggers and consumed by the reader."""

    __tablename__ = "customer_cache_state"

    id: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, server_default=text("true")
    )
    dirty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class CustomerCache(Base, TimestampMixin):
    """One deduplicated person, rebuilt from customer-bearing base records."""

    __tablename__ = "customer_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    phone_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    order_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    earliest_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_revenue: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    aov: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )


class CustomerOrderCache(Base):
    """The exact order membership behind one cached customer profile."""

    __tablename__ = "customer_order_cache"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_cache.id", ondelete="CASCADE"),
        primary_key=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
        unique=True,
    )


class CustomerDeliveryAreaCache(Base, TimestampMixin):
    """One geocoded delivery order behind the customer delivery-area map.

    This remains a cache rather than a second order/address source of truth:
    ``customer_service.refresh_if_dirty`` rebuilds it from the canonical order
    ledger together with ``customer_cache``. Keeping the point at order grain
    lets the map answer distinct-customer density, revenue, and AOV for any
    date range without reading or parsing address JSON on every request.
    """

    __tablename__ = "customer_delivery_area_cache"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customer_cache.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    order_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    order_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    source_channel: Mapped[str] = mapped_column(String(32), nullable=False)
