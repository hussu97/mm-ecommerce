import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.models.user import User
from app.schemas.address import AddressCreate, AddressResponse, AddressUpdate
from app.services import address_service

router = APIRouter()


@router.get("", response_model=list[AddressResponse])
async def list_addresses(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List all saved addresses for the current user."""
    return await address_service.get_all(db, current_user.id)


@router.get("/{address_id}", response_model=AddressResponse)
async def get_address(
    address_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get a single address by ID."""
    return await address_service.get_by_id(db, current_user.id, address_id)


@router.post("", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
async def create_address(
    data: AddressCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Add a new address."""
    return await address_service.create(db, current_user.id, data)


@router.put("/{address_id}", response_model=AddressResponse)
async def update_address(
    address_id: uuid.UUID,
    data: AddressUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update an existing address."""
    return await address_service.update(db, current_user.id, address_id, data)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(
    address_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Delete an address."""
    await address_service.delete(db, current_user.id, address_id)


@router.put("/{address_id}/default", response_model=AddressResponse)
async def set_default_address(
    address_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Set an address as the default."""
    return await address_service.set_default(db, current_user.id, address_id)
