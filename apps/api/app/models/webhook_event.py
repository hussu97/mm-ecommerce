from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow


class WebhookEvent(Base, UUIDMixin):
    """Tracks processed webhook events to prevent duplicate handling."""

    __tablename__ = "webhook_events"

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
