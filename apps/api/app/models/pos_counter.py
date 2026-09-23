"""
Local-first counter checkout: the config bundles terminals price against, and
the synced sales that could not be booked.

See `app/services/pos/counter_bundle_service.py` and
`app/services/pos/counter_ingest_service.py`. Migration 284.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PosConfigBundle(Base):
    """
    One published counter config bundle, content-addressed.

    `hash` is the sha256 of the bundle's canonical hashed body — the ETag the
    terminal holds and cites on every sale it syncs. Persisted the first time it
    is served so ingest can re-price a sale with exactly the inputs the device
    priced it with, whatever has been edited since. Pruned 120 days after it was
    last served (`counter_bundle_service.prune`).
    """

    __tablename__ = "pos_config_bundles"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    engine_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The hashed body exactly as served (JSON-mode: money as strings).
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_served_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class CounterSaleQuarantine(Base):
    """
    A synced counter sale that could not be booked as an order.

    A paid sale is never simply rejected: the money has moved and a receipt has
    printed. When a sale is structurally wrong for this server — another
    branch's device, a till that is not this device's, a ticket prefix that is
    not this till's, a payment method or cashier that does not exist — it is
    parked here (the device is told 202 and clears it from its outbox) for a
    manager to resolve from the console.

    `id` is the client's order id, so a retried sync of the same sale lands on
    the same row rather than a second one.
    """

    __tablename__ = "counter_sale_quarantine"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: The sale exactly as the device sent it.
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    payload_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["CounterSaleQuarantine", "PosConfigBundle"]
