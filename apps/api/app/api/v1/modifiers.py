from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.permissions import require, require_any
from app.models.user import User
from app.schemas.modifier import (
    ModifierCreate,
    ModifierOptionCreate,
    ModifierOptionUpdate,
    ModifierResponse,
    ModifierUpdate,
)
from app.services.catalog import modifier_service

router = APIRouter()


@router.get("", response_model=list[ModifierResponse])
async def list_modifiers(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_any("pos.register.access", "catalogue.manage")),
):
    """List all modifiers with their options.

    An admin/catalogue surface, not a storefront one — the storefront reads a
    product's modifiers embedded in the product response and never calls this.
    It carried no auth at all, so any caller could enumerate the catalogue's
    modifier structure; it now requires the register or a catalogue manager.
    """
    return await modifier_service.get_all(db, include_inactive=include_inactive)


@router.post("", response_model=ModifierResponse, status_code=status.HTTP_201_CREATED)
async def create_modifier(
    data: ModifierCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Create a modifier (admin only)."""
    return await modifier_service.create(db, data)


@router.get("/{modifier_id}", response_model=ModifierResponse)
async def get_modifier(
    modifier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_any("pos.register.access", "catalogue.manage")),
):
    """Get a modifier by ID (admin/register surface — see `list_modifiers`)."""
    return await modifier_service.get_by_id(db, modifier_id)


@router.put("/{modifier_id}", response_model=ModifierResponse)
async def update_modifier(
    modifier_id: uuid.UUID,
    data: ModifierUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
):
    """Update a modifier (admin only)."""
    return await modifier_service.update(db, modifier_id, data)


@router.delete("/{modifier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_modifier(
    modifier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
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
    _admin: User = Depends(require("catalogue.manage")),
):
    """Add an option to a modifier (admin only)."""
    return await modifier_service.add_option(db, modifier_id, data)


@router.put("/{modifier_id}/options/{option_id}", response_model=ModifierResponse)
async def update_modifier_option(
    modifier_id: uuid.UUID,
    option_id: uuid.UUID,
    data: ModifierOptionUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("catalogue.manage")),
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
    _admin: User = Depends(require("catalogue.manage")),
):
    """Delete a modifier option (admin only)."""
    return await modifier_service.delete_option(db, modifier_id, option_id)
