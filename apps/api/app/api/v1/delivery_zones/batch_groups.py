"""Batch groups: the named runs a zone's orders are collected into."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.delivery_batch import DeliveryBatchGroup
from app.models.delivery_polygon import DeliveryPolygon
from app.models.user import User
from app.services import audit_service

from ._shared import _load_group, _windows_of
from .schemas import BatchGroupResponse, BatchGroupUpdate

router = APIRouter()


@router.get("/batch-groups", response_model=list[BatchGroupResponse])
async def list_batch_groups(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """
    Every schedule, with the zones on it.

    Declared beside `/{polygon_id}` routes, so the literal path has to be
    matched before anything that would read "batch-groups" as an id.
    """
    groups = (
        (await db.execute(select(DeliveryBatchGroup).order_by(DeliveryBatchGroup.name)))
        .scalars()
        .all()
    )
    out: list[BatchGroupResponse] = []
    for group in groups:
        zones = (
            (
                await db.execute(
                    select(DeliveryPolygon.name)
                    .where(DeliveryPolygon.batch_group_id == group.id)
                    .order_by(DeliveryPolygon.display_order)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            BatchGroupResponse.of(group, list(zones), await _windows_of(db, group.id))
        )
    return out


@router.put("/batch-groups/{group_id}", response_model=BatchGroupResponse)
async def update_batch_group(
    group_id: uuid.UUID,
    data: BatchGroupUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Change how long after a run leaves that its last box is through a door.

    The other half of what the checkout quotes a batched zone, and until now the
    half that needed a deploy: the window said when the van goes, and this says
    how long it then takes. Unlike a fee it takes effect immediately and is not
    versioned — a wrong number here delays nothing and overcharges nobody, it
    just says the wrong time, and the fix is to say the right one.

    Orders already quoted are untouched. What the shop said out loud is a
    record, not a derivation (`order_deliveries` keeps it), so moving this
    number moves the next promise rather than rewriting the last one.
    """
    group = await _load_group(db, group_id)
    before = {
        "delivery_minutes_after_dispatch": group.delivery_minutes_after_dispatch,
        "is_active": group.is_active,
    }
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(group, field, value)
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch_group",
        entity_id=str(group.id),
        entity_label=group.name,
        admin=admin,
        changes={
            "from": before,
            "to": {
                "delivery_minutes_after_dispatch": (
                    group.delivery_minutes_after_dispatch
                ),
                "is_active": group.is_active,
            },
        },
        request=request,
    )

    zones = (
        (
            await db.execute(
                select(DeliveryPolygon.name)
                .where(DeliveryPolygon.batch_group_id == group.id)
                .order_by(DeliveryPolygon.display_order)
            )
        )
        .scalars()
        .all()
    )
    return BatchGroupResponse.of(group, list(zones), await _windows_of(db, group.id))
