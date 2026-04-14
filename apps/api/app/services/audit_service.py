from __future__ import annotations

import logging
import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User

__all__ = ["log_action"]

logger = logging.getLogger(__name__)


async def log_action(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    entity_label: str,
    admin: User,
    changes: dict | None = None,
    request: Request | None = None,
) -> None:
    """Append an immutable audit log entry.

    Fire-and-forget: any exception is caught and logged so it never
    disrupts the primary business operation.

    Args:
        action: One of CREATE | UPDATE | DELETE | STATUS_CHANGE.
        entity_type: Logical domain (product, order, category, promo_code).
        entity_id: The primary identifier (slug, order_number, UUID str).
        entity_label: Human-readable description for the log UI.
        admin: The authenticated admin user performing the action.
        changes: Optional dict — for CREATE/DELETE include the full payload;
                 for UPDATE include {"before": {...}, "after": {...}}.
        request: FastAPI Request — used only to extract the client IP.
    """
    try:
        ip: str | None = None
        if request is not None:
            forwarded = request.headers.get("X-Forwarded-For")
            ip = (
                forwarded.split(",")[0].strip()
                if forwarded
                else request.client.host
                if request.client
                else None
            )

        entry = AuditLog(
            id=uuid.uuid4(),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            admin_id=admin.id,
            admin_email=admin.email,
            changes=changes,
            ip_address=ip,
        )
        db.add(entry)
        await db.flush()
    except Exception:
        logger.exception(
            "audit_service.log_action failed — action=%s entity_type=%s entity_id=%s",
            action,
            entity_type,
            entity_id,
        )
