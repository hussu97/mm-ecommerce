"""The one row of replenishment tuning, read as the engine's settings."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.replenishment import ReplenishmentSettings
from app.services.inventory.replenishment import engine


async def load_settings(db: AsyncSession) -> ReplenishmentSettings:
    """The settings row, created with defaults if the migration's seed is gone."""
    row = (
        await db.execute(select(ReplenishmentSettings).limit(1))
    ).scalar_one_or_none()
    if row is None:
        row = ReplenishmentSettings()
        db.add(row)
        await db.flush()
        await db.refresh(row)
    return row


def to_engine(
    row: ReplenishmentSettings, *, bucket_hours: int | None = None
) -> engine.Settings:
    return engine.Settings(
        bucket_hours=bucket_hours or row.bucket_hours,
        service_level=float(row.service_level),
        pool_weight=float(row.production_branch_weight),
        same_day_ready_time=row.same_day_ready_time,
        next_day_category_ids=frozenset(row.next_day_category_ids or ()),
        half_life_days=row.half_life_days,
        window_days=row.window_days,
    )
