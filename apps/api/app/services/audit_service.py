from __future__ import annotations

import logging
import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_ip import client_ip
from app.models.audit_log import AuditLog
from app.models.user import User

__all__ = ["log_action", "log_actor_action"]

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
    """Append an immutable audit log entry for an authenticated admin user.

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
    await log_actor_action(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=entity_label,
        actor_id=admin.id,
        actor_email=admin.email,
        changes=changes,
        request=request,
    )


async def log_actor_action(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    entity_label: str,
    actor_id: uuid.UUID,
    actor_email: str,
    changes: dict | None = None,
    request: Request | None = None,
) -> None:
    """Append an audit entry for an actor that is not an admin user.

    The `admin_id`/`admin_email` columns are NOT NULL, so an action taken with no
    signed-in user (a terminal claiming its pairing code) still needs an actor to
    record. It is recorded as the acting entity itself — the device's id and a
    `device:<reference>` label — rather than inventing a user or relaxing the
    schema. Same immutable trail, same fire-and-forget contract as `log_action`.
    """
    try:
        # The rightmost `X-Forwarded-For` hop — the address nginx actually saw,
        # not the leftmost one the caller can set for itself. This trail is read
        # precisely when a change is disputed, so the IP it records must be the
        # one hop that cannot be chosen by the party being audited. See
        # `app.core.request_ip.client_ip`.
        ip = client_ip(request) if request is not None else None

        entry = AuditLog(
            id=uuid.uuid4(),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            admin_id=actor_id,
            admin_email=actor_email,
            changes=changes,
            ip_address=ip,
        )
        db.add(entry)
        await db.flush()
    except Exception:
        logger.exception(
            "audit_service.log_actor_action failed — action=%s entity_type=%s "
            "entity_id=%s",
            action,
            entity_type,
            entity_id,
        )
