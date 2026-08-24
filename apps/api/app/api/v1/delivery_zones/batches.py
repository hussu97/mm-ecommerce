"""Batches: the runs themselves, and dispatching one before its window closes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import require
from app.models.delivery_batch import BatchStatusEnum, DeliveryBatch, DeliveryBatchGroup
from app.models.order import Order
from app.models.order_delivery import OrderDelivery
from app.models.user import User
from app.services import audit_service
from app.services.delivery import batching_service

from .schemas import BatchResponse

router = APIRouter()


@router.get("/batches", response_model=list[BatchResponse])
async def list_batches(
    status_filter: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """Runs waiting to leave and runs already gone, most imminent first."""
    stmt = (
        select(DeliveryBatch)
        .order_by(DeliveryBatch.dispatch_at.desc())
        .limit(min(limit, 200))
    )
    if status_filter:
        stmt = stmt.where(DeliveryBatch.status == status_filter)
    batches = list((await db.execute(stmt)).scalars().all())
    if not batches:
        return []

    group_names = dict(
        (
            await db.execute(
                select(DeliveryBatchGroup.id, DeliveryBatchGroup.name).where(
                    DeliveryBatchGroup.id.in_({b.group_id for b in batches})
                )
            )
        ).all()
    )
    rows = (
        await db.execute(
            select(OrderDelivery.batch_id, OrderDelivery.zone_name, Order.order_number)
            .join(Order, Order.id == OrderDelivery.order_id)
            .where(OrderDelivery.batch_id.in_({b.id for b in batches}))
            .order_by(OrderDelivery.stop_sequence, Order.order_number)
        )
    ).all()
    numbers: dict[uuid.UUID, list[str]] = {}
    on_run: dict[uuid.UUID, list[str]] = {}
    for batch_id, zone_name, order_number in rows:
        numbers.setdefault(batch_id, []).append(order_number)
        zones_here = on_run.setdefault(batch_id, [])
        if zone_name and zone_name not in zones_here:
            zones_here.append(zone_name)

    return [
        BatchResponse.of(
            b,
            # What is actually on the run. Falls back to the group that opened
            # it for a batch that has not collected anything yet.
            ", ".join(on_run.get(b.id) or []) or group_names.get(b.group_id),
            numbers.get(b.id, []),
        )
        for b in batches
    ]


@router.post("/batches/{batch_id}/dispatch", response_model=BatchResponse)
async def dispatch_batch_now(
    batch_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Send a run early, or retry one the courier refused.

    The sweeper fires these on schedule and retries a refusal a few times on its
    own; this is for the shop deciding it is not worth waiting, and for the run
    that has already exhausted those attempts.

    Pressing it resets the ladder. Somebody doing this by hand has almost always
    just changed something the automatic attempts could not — topped up the
    wallet, fixed an address — so the run deserves the full set of tries again
    rather than the one that was left.
    """
    batch = await db.get(DeliveryBatch, batch_id)
    if batch is None:
        raise NotFoundError("Batch not found")
    if batch.status == BatchStatusEnum.DISPATCHED.value and not batch.next_attempt_at:
        raise ConflictError("This run has already gone out.")

    batch.attempt_count = 0
    await batching_service.dispatch_batch(db, batch)
    await db.flush()
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_batch",
        entity_id=str(batch.id),
        entity_label=f"{batch.window_label or 'run'} · {batch.stop_count} drops",
        admin=admin,
        changes={
            "courier_order_id": batch.courier_order_id,
            "status": batch.status,
            "error": batch.last_error,
        },
        request=request,
    )
    group = await db.get(DeliveryBatchGroup, batch.group_id)
    return BatchResponse.of(batch, group.name if group else None, [])
