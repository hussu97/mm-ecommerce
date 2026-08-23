"""
The loaders more than one route group needs.

`_load_group` and `_windows_of` are used by both the batch-group screen and
the batch-window screen; everything else in this package keeps its helpers
beside the routes that call them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.delivery_batch import DeliveryBatchGroup, DeliveryBatchWindow


async def _load_group(db: AsyncSession, group_id: uuid.UUID) -> DeliveryBatchGroup:
    group = await db.get(DeliveryBatchGroup, group_id)
    if group is None:
        raise NotFoundError("Batch group not found")
    return group


async def _windows_of(
    db: AsyncSession, group_id: uuid.UUID
) -> list[DeliveryBatchWindow]:
    result = await db.execute(
        select(DeliveryBatchWindow)
        .where(DeliveryBatchWindow.group_id == group_id)
        .order_by(DeliveryBatchWindow.start_hour, DeliveryBatchWindow.start_minute)
    )
    return list(result.scalars().all())
