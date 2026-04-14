from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, utcnow

__all__ = ["AuditLog"]


class AuditLog(Base, UUIDMixin):
    """Immutable trail of every admin mutation (create/update/delete)."""

    __tablename__ = "audit_logs"

    # What happened
    action: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # CREATE | UPDATE | DELETE | STATUS_CHANGE

    # What was affected
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # product | order | category | promo_code
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_label: Mapped[str] = mapped_column(String(500), nullable=False)

    # Who did it
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    admin_email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Payload — before/after for UPDATE; full data for CREATE/DELETE
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_entity_type", "entity_type"),
        Index("ix_audit_logs_admin_id", "admin_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )
