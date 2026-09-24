"""The replenishment forecast behind the transfer & production form's guide
numbers, its shadow history, and its settings. Read-only except the settings."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require
from app.models.user import User
from app.schemas.replenishment import (
    ForecastHistoryResponse,
    ForecastResponse,
    ReplenishmentSettingsResponse,
    ReplenishmentSettingsUpdate,
)
from app.services.inventory.replenishment import history, service
from app.services.inventory.replenishment.settings import load_settings
from app.services.pos import business_day_service

router = APIRouter()


@router.get("/forecast", response_model=ForecastResponse)
async def get_forecast(
    source_branch_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("inventory.transfers.manage")),
):
    """Forecast transfer quantity per destination and production quantity per
    produced good, for the source branch's current business day, as of now.
    The intraday bucket width is the global setting."""
    return await service.current_forecast(db, source_branch_id=source_branch_id)


@router.get("/history", response_model=ForecastHistoryResponse)
async def get_history(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    branch_id: uuid.UUID | None = Query(default=None),
    item_id: uuid.UUID | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(transfer|retain|production)$"),
    mode: str | None = Query(default=None, pattern="^(scheduled|backtest)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("inventory.transfers.manage")),
):
    """Daily forecast snapshots against what was actually transferred and
    produced, with accuracy scores over the filtered rows."""
    tz = await business_day_service.resolve_timezone(db)
    today = business_day_service.shop_today(tz)
    return await history.history_view(
        db,
        date_from=date_from or today - timedelta(days=14),
        date_to=date_to or today,
        branch_id=branch_id,
        item_id=item_id,
        kind=kind,
        mode=mode,
    )


@router.get("/settings", response_model=ReplenishmentSettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("inventory.transfers.manage")),
):
    return await load_settings(db)


@router.put("/settings", response_model=ReplenishmentSettingsResponse)
async def update_settings(
    body: ReplenishmentSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("inventory.transfers.manage")),
):
    row = await load_settings(db)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "production_branch_id" or value is not None:
            setattr(row, field, value)
    await db.flush()
    await db.refresh(row)
    return row
