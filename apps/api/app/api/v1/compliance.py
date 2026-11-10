"""The shop's expiring paperwork — the console's warning popup reads this.

Super-admin only: the list names employees and their visa / labour-card
numbers alongside the trade licences.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require_superadmin
from app.models.user import User
from app.schemas.employee import ExpiringDocumentsResponse
from app.services.people import expiry_service

router = APIRouter()


@router.get("/expiring-documents", response_model=ExpiringDocumentsResponse)
async def expiring_documents(
    within_days: int = Query(expiry_service.DEFAULT_WITHIN_DAYS, ge=0, le=365),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_superadmin()),
):
    """Active employees' current visa / labour card / OHC and active legal
    entities' current trade licence that expire within *within_days* days (or
    already have), soonest first."""
    return await expiry_service.expiring_documents(db, within_days=within_days)
