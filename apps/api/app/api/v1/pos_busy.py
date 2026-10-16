"""Busy mode from the register — the switch and the banner it drives.

Scoped to the terminal's own branch: a device token names one shop, and taking
another shop offline from this counter is not a feature. Mounted on both the
storefront API and the register's own app (`pos_router`), like every till path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.device import Device
from app.models.user import User
from app.schemas.pos.busy_mode import BusyStatusResponse, SetBusyRequest
from app.services.pos import busy_mode_service

from .devices import get_current_device

router = APIRouter()


async def _branch_of(db: AsyncSession, device: Device) -> Branch:
    branch = await db.get(Branch, device.branch_id)
    if branch is None:
        raise NotFoundError("This terminal is not assigned to a branch")
    return branch


@router.get("/busy", response_model=BusyStatusResponse)
async def get_busy(
    db: AsyncSession = Depends(get_db),
    device: Device = Depends(get_current_device),
    _: User = Depends(require("pos.register.access")),
) -> BusyStatusResponse:
    """Busy mode at this terminal's branch, with a chip per aggregator outlet."""
    branch = await _branch_of(db, device)
    return busy_mode_service.to_response(await busy_mode_service.status(db, branch))


@router.put("/busy", response_model=BusyStatusResponse)
async def set_busy(
    data: SetBusyRequest,
    db: AsyncSession = Depends(get_db),
    device: Device = Depends(get_current_device),
    user: User = Depends(require("pos.branch.busy")),
) -> BusyStatusResponse:
    """Turn busy mode on or off. The website follows at once; the aggregator
    outlets follow within seconds (the chips show each one land)."""
    branch = await _branch_of(db, device)
    state = await busy_mode_service.set_busy(
        db,
        branch,
        data.busy,
        actor_id=user.id,
        device_id=device.id,
        source="pos",
    )
    return busy_mode_service.to_response(state)
