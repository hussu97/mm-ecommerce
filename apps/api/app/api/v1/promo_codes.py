from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_admin_user, get_db, get_optional_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.promo_code import (
    PromoCodeAdvertResponse,
    PromoCodeBulkCreate,
    PromoCodeBulkResponse,
    PromoCodeCreate,
    PromoCodeResponse,
    PromoCodeUpdate,
    PromoCodeValidateRequest,
    PromoCodeValidateResponse,
)
from app.services import audit_service, promo_code_service

router = APIRouter()


@router.post("/validate", response_model=PromoCodeValidateResponse)
@limiter.limit("20/minute")
async def validate_promo_code(
    request: Request,
    data: PromoCodeValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
):
    """
    Validate a promo code and return the discount amount.

    Deliberately open to guests — a customer types a code before they have an
    account — but rate limited, because an unauthenticated endpoint that
    answers "is this a real code?" is otherwise an offline-quality oracle for
    enumerating the whole code space.
    """
    return await promo_code_service.validate(
        db,
        data.code,
        data.order_subtotal,
        user_id=current_user.id if current_user else None,
        # The account's own email wins over anything typed: a signed-in customer
        # entering somebody else's address must not thereby become a new one.
        email=(current_user.email if current_user else None) or data.email,
        phone=data.phone,
        delivery_method=data.delivery_method,
        # Never from here. This endpoint answers "may I have this discount, and
        # what is left to do for it" — it does not hand one out, so refusing on
        # an unverified phone would only take the code off a basket that is
        # nowhere near being an order yet. `create_order` enforces it.
        enforce_phone_verification=False,
    )


@router.get("/featured", response_model=PromoCodeAdvertResponse | None)
@limiter.limit("60/minute")
async def featured_promo_code(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    The new-customer coupon currently being advertised, or `null` if none is.

    Exists so the storefront can show the offer instead of expecting a customer
    to already know the code. Which means the coupon's own row is the single
    place its terms live: change the percentage, the cap or the number of orders
    in the admin and every surface that quotes them changes with it. Hardcoding
    them in the browser is how a campaign ends up advertised at 15% and redeemed
    at 10%.

    Public, because the cart page it feeds is public and a guest is exactly who
    the offer is for. Narrow, because public: it answers with one row, only the
    fields a shopper acts on, and only while that row is genuinely redeemable —
    never an inactive code, never one outside its window or past its ceiling,
    and never a usage count. Rate limited for the same reason `/validate` is:
    an unauthenticated endpoint that names a live code should not also be a
    free, unlimited one.

    Declared before `/{code}` so "featured" is not read as a coupon code.
    """
    return await promo_code_service.advertisable(db)


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


@router.post(
    "/bulk",
    response_model=PromoCodeBulkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_promo_codes_bulk(
    request: Request,
    data: PromoCodeBulkCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """
    Generate a batch of unique single-use coupons.

    Declared before `/{code}` so "bulk" is not read as a coupon code.
    """
    codes = await promo_code_service.create_bulk(db, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="promo_code",
        entity_id=data.prefix,
        entity_label=f"{len(codes)} codes with prefix {data.prefix}",
        admin=admin,
        changes={"prefix": data.prefix, "count": len(codes)},
        request=request,
    )
    return PromoCodeBulkResponse(created=len(codes), codes=codes)
