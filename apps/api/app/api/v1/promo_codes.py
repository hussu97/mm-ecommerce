from fastapi import APIRouter, Depends, Query, Request, status
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
from app.services import audit_service, promo_code_service

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
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_admin_user),
):
    """List all promo codes (admin only)."""
    return await promo_code_service.get_all(db, include_inactive=include_inactive)


@router.post("", response_model=PromoCodeResponse, status_code=status.HTTP_201_CREATED)
async def create_promo_code(
    request: Request,
    data: PromoCodeCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Create a new promo code (admin only)."""
    result = await promo_code_service.create(db, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="promo_code",
        entity_id=result.code,
        entity_label=result.code,
        admin=admin,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return result


@router.put("/{code}", response_model=PromoCodeResponse)
async def update_promo_code(
    request: Request,
    code: str,
    data: PromoCodeUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Update a promo code (admin only)."""
    result = await promo_code_service.update(db, code, data)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="promo_code",
        entity_id=code,
        entity_label=code,
        admin=admin,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return result


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_promo_code(
    request: Request,
    code: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """Delete a promo code (admin only)."""
    await promo_code_service.delete(db, code)
    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="promo_code",
        entity_id=code,
        entity_label=code,
        admin=admin,
        changes={"deleted_code": code},
        request=request,
    )
