from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow


class EmailLog(Base, UUIDMixin):
    """Persists every email send attempt for visibility and debugging."""

    __tablename__ = "email_logs"

    template: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    order_number: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True
    )  # sent | failed | skipped
    resend_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
