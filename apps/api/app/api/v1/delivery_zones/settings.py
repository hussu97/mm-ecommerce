"""The shop-wide delivery settings row."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.services import audit_service, delivery_service

from .schemas import DeliverySettingsResponse, DeliverySettingsUpdate

router = APIRouter()


@router.get("/settings", response_model=DeliverySettingsResponse)
async def get_delivery_settings(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """
    The three delivery numbers that are not a property of any zone.

    They live here rather than on their own screen because there is nothing
    else left to configure about delivery: the free-delivery threshold is
    deliberately identical everywhere, pickup has no zone, and the default is
    what a pin outside every shape on the map gets charged.
    """
    return DeliverySettingsResponse.of(await delivery_service.get_settings(db))


@router.put("/settings", response_model=DeliverySettingsResponse)
async def update_delivery_settings(
    data: DeliverySettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """Change them. Unlike a zone fee, these take effect immediately."""
    settings = await delivery_service.get_settings(db)
    before = DeliverySettingsResponse.of(settings).model_dump()

    for field, value in data.model_dump(exclude_none=True).items():
        setattr(settings, field, value)
    # `exclude_none` above is right for every field except this one, where null
    # is a real instruction: it switches the small-basket fee off. Without this,
    # an admin could turn the fee on and never turn it back off.
    if "low_order_threshold" in data.model_fields_set:
        settings.low_order_threshold = data.low_order_threshold
    # `get_settings` invents a row when the table is empty, so this has to be an
    # add rather than an assumption that the object is already tracked.
    db.add(settings)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_settings",
        entity_id=str(settings.id),
        entity_label="Delivery settings",
        admin=admin,
        changes={
            "from": before,
            "to": DeliverySettingsResponse.of(settings).model_dump(),
        },
        request=request,
    )
    return DeliverySettingsResponse.of(settings)
