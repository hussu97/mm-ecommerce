from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db
from app.models.user import User
from app.schemas.promo_code import (
    PromoCodeCreate,
    PromoCodeResponse,
    PromoCodeUpdate,
    PromoCodeValidateRequest,
    PromoCodeValidateResponse,
)
from app.services import promo_code_service

router = APIRouter()


@router.post("/validate", response_model=PromoCodeValidateResponse)
async def validate_promo_code(
    data: PromoCodeValidateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Validate a promo code and return the discount amount."""
    return await promo_code_service.validate(db, data.code, data.order_subtotal)


@router.get("", response_model=list[PromoCodeResponse])
async def list_promo_codes(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """List all promo codes (admin only)."""
    return await promo_code_service.get_all(db)


@router.post("", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_promo_code(
    data: PromoCodeCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Create a new promo code (admin only)."""
    return await promo_code_service.create(db, data)


@router.put("/{code}", response_model=PromoCodeResponse)
async def update_promo_code(
    code: str,
    data: PromoCodeUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Update a promo code (admin only)."""
    return await promo_code_service.update(db, code, data)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """Delete a promo code (admin only)."""
    await promo_code_service.delete(db, code)
