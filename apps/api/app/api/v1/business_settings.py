"""Business-wide POS settings — receipt, kitchen, inventory and cashier behaviour."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.permissions import require
from app.models import BusinessSettings
from app.models.user import User
from app.schemas.pos import BusinessSettingsResponse, BusinessSettingsUpdate
from app.services import audit_service

router = APIRouter()


async def get_or_create_settings(db: AsyncSession) -> BusinessSettings:
    """
    Settings are a singleton. Created lazily on first read so a fresh deployment
    does not need a seed step.
    """
    existing = (
        (
            await db.execute(
                select(BusinessSettings).order_by(BusinessSettings.created_at)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    created = BusinessSettings()
    db.add(created)
    await db.flush()
    await db.refresh(created)
    return created


@router.get("", response_model=BusinessSettingsResponse)
async def read_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Readable by any signed-in user — the POS terminal needs these at launch."""
    return await get_or_create_settings(db)


@router.put("", response_model=BusinessSettingsResponse)
async def update_settings(
    request: Request,
    data: BusinessSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("admin.settings.manage")),
):
    current = await get_or_create_settings(db)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(current, field, value)
    await db.flush()
    await db.refresh(current)

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="business_settings",
        entity_id=str(current.id),
        entity_label=current.business_name,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return current
