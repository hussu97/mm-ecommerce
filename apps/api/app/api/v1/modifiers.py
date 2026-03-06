from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.modifier import (
    ModifierCreate,
    ModifierOptionCreate,
    ModifierOptionUpdate,
    ModifierResponse,
    ModifierUpdate,
)
from app.services import modifier_service

router = APIRouter()


@router.get("", response_model=list[ModifierResponse])
async def list_modifiers(db: AsyncSession = Depends(get_db)):
    """List all modifiers with their options."""
    return await modifier_service.get_all(db)


@router.post("", response_model=ModifierResponse, status_code=status.HTTP_201_CREATED)
async def create_modifier(
    data: ModifierCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Create a modifier (admin only)."""
    return await modifier_service.create(db, data)


@router.get("/{modifier_id}", response_model=ModifierResponse)
async def get_modifier(modifier_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a modifier by ID."""
    return await modifier_service.get_by_id(db, modifier_id)


@router.put("/{modifier_id}", response_model=ModifierResponse)
async def update_modifier(
    modifier_id: uuid.UUID,
    data: ModifierUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a modifier (admin only)."""
    return await modifier_service.update(db, modifier_id, data)


@router.delete("/{modifier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_modifier(
    modifier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a modifier (admin only)."""
    await modifier_service.delete(db, modifier_id)


@router.post(
    "/{modifier_id}/options",
    response_model=ModifierResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_modifier_option(
    modifier_id: uuid.UUID,
    data: ModifierOptionCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Add an option to a modifier (admin only)."""
    return await modifier_service.add_option(db, modifier_id, data)


@router.put("/{modifier_id}/options/{option_id}", response_model=ModifierResponse)
async def update_modifier_option(
    modifier_id: uuid.UUID,
    option_id: uuid.UUID,
    data: ModifierOptionUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a modifier option (admin only)."""
    return await modifier_service.update_option(db, modifier_id, option_id, data)


@router.delete(
    "/{modifier_id}/options/{option_id}",
    response_model=ModifierResponse,
)
async def delete_modifier_option(
    modifier_id: uuid.UUID,
    option_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a modifier option (admin only)."""
    return await modifier_service.delete_option(db, modifier_id, option_id)
