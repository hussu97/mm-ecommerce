from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, status_vocabulary, utcnow


class EmailLogStatusEnum(str, enum.Enum):
    """What `email_service._send` decided happened.

    `skipped` is not a failure: no recipient, a malformed address, or sending
    switched off. It is separated from `failed` so a quiet mailbox can be told
    apart from a broken one.
    """

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class EmailLog(Base, UUIDMixin):
    """Persists every email send attempt for visibility and debugging."""

    __tablename__ = "email_logs"
    # Migration 138. The vocabulary used to live in a trailing comment on the
    # column, which is not a thing the database can check.
    __table_args__ = (status_vocabulary("email_logs", "status", EmailLogStatusEnum),)

    template: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    order_number: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    resend_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
