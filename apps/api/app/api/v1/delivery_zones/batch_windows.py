"""Batch windows: when a group's run closes and dispatches."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import require
from app.models.courier import Courier
from app.models.delivery_batch import DeliveryBatchWindow
from app.models.user import User
from app.services import audit_service, batching_service

from ._shared import _load_group, _windows_of
from .schemas import BatchWindowResponse, BatchWindowWrite

router = APIRouter()


def _reject_overlaps(windows: list[DeliveryBatchWindow]) -> None:
    clash = batching_service.overlapping(windows)
    if clash is None:
        return
    first, second = clash
    raise ConflictError(
        f'"{first.label}" and "{second.label}" both cover the same time. '
        "Two batches claiming one minute makes it a coin toss which run an "
        "order joins.",
    )


@router.get(
    "/batch-groups/{group_id}/batch-windows", response_model=list[BatchWindowResponse]
)
async def list_batch_windows(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """When orders in this group travel together. All times are Dubai time."""
    await _load_group(db, group_id)
    return [BatchWindowResponse.of(w) for w in await _windows_of(db, group_id)]


@router.post(
    "/batch-groups/{group_id}/batch-windows",
    response_model=BatchWindowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_batch_window(
    group_id: uuid.UUID,
    data: BatchWindowWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Add a slot to a group's schedule.

    Only a courier that can carry several of our orders in one booking may have
    one — `supports_batching` on the courier row. A schedule on anything else is
    a setting that does nothing, which is worse than an absent one because
    somebody will come to rely on it.
    """
    group = await _load_group(db, group_id)
    courier = (
        await db.execute(select(Courier).where(Courier.code == group.courier_code))
    ).scalar_one_or_none()
    if courier is None or not courier.supports_batching:
        raise BadRequestError(
            f"'{group.name}' books {group.courier_code}, which cannot carry "
            "several of our orders in one booking — so it has no run to share.",
        )

    window = DeliveryBatchWindow(group_id=group_id, **data.model_dump())
    _reject_overlaps([*await _windows_of(db, group_id), window])
    db.add(window)
    await db.flush()

    # Anything already waiting is re-derived, so adding a slot picks up the
    # orders that fell into the gap it just filled.
    await batching_service.reschedule_group(db, group_id)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=f"{group.name} · {window.label}",
        admin=admin,
        changes=data.model_dump(),
        request=request,
    )
    return BatchWindowResponse.of(window)


@router.put("/batch-windows/{window_id}", response_model=BatchWindowResponse)
async def update_batch_window(
    window_id: uuid.UUID,
    data: BatchWindowWrite,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Move a slot, and move everything still waiting on it.

    An order whose new window has already closed goes out on its own rather
    than waiting until tomorrow for a slot that has been and gone.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise NotFoundError("Batch window not found")

    before = BatchWindowResponse.of(window).model_dump()
    for field, value in data.model_dump().items():
        setattr(window, field, value)
    _reject_overlaps(await _windows_of(db, window.group_id))
    await db.flush()

    moved = await batching_service.reschedule_group(db, window.group_id)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=window.label,
        admin=admin,
        changes={"from": before, "to": data.model_dump(), "rescheduled": moved},
        request=request,
    )
    return BatchWindowResponse.of(window)


@router.delete("/batch-windows/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_batch_window(
    window_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Remove a slot. Orders waiting on it are re-derived, and any that no longer
    fall in a window go out on their own rather than being stranded.
    """
    window = await db.get(DeliveryBatchWindow, window_id)
    if window is None:
        raise NotFoundError("Batch window not found")

    group_id = window.group_id
    label = window.label
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="delivery_batch_window",
        entity_id=str(window.id),
        entity_label=label,
        admin=admin,
        request=request,
    )
    await db.delete(window)
    await db.flush()
    await batching_service.reschedule_group(db, group_id)
