from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.promo_code import DiscountTypeEnum, PromoCode
from app.schemas.promo_code import (
    PromoCodeCreate,
    PromoCodeResponse,
    PromoCodeUpdate,
    PromoCodeValidateResponse,
)


def _calc_discount(promo: PromoCode, subtotal: Decimal) -> Decimal:
    if promo.discount_type == DiscountTypeEnum.PERCENTAGE:
        amount = (subtotal * promo.discount_value / Decimal("100")).quantize(Decimal("0.01"))
    else:
        amount = min(promo.discount_value, subtotal)
    return amount


async def validate(
    db: AsyncSession,
    code: str,
    subtotal: Decimal,
) -> PromoCodeValidateResponse:
    """Validate a promo code and return the discount amount (does NOT increment uses)."""
    result = await db.execute(select(PromoCode).where(PromoCode.code == code.upper()))
    promo = result.scalar_one_or_none()

    if not promo:
        return PromoCodeValidateResponse(valid=False, message="Promo code not found")

    if not promo.is_active:
        return PromoCodeValidateResponse(valid=False, message="Promo code is not active")

    now = datetime.now(timezone.utc)
    if promo.valid_from and now < promo.valid_from:
        return PromoCodeValidateResponse(valid=False, message="Promo code is not yet valid")

    if promo.valid_until and now > promo.valid_until:
        return PromoCodeValidateResponse(valid=False, message="Promo code has expired")

    if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
        return PromoCodeValidateResponse(valid=False, message="Promo code has reached its usage limit")

    if promo.min_order_amount and subtotal < promo.min_order_amount:
        return PromoCodeValidateResponse(
            valid=False,
            message=f"Minimum order amount of {promo.min_order_amount} AED required",
        )

    discount = _calc_discount(promo, subtotal)
    return PromoCodeValidateResponse(valid=True, discount_amount=discount)


async def get_promo(db: AsyncSession, code: str) -> PromoCode:
    """Fetch and validate a promo for use during order creation. Raises on invalid."""
    result = await db.execute(select(PromoCode).where(PromoCode.code == code.upper()))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")
    return promo


async def get_all(db: AsyncSession) -> list[PromoCodeResponse]:
    result = await db.execute(select(PromoCode).order_by(PromoCode.created_at.desc()))
    return [PromoCodeResponse.model_validate(p) for p in result.scalars().all()]


async def create(db: AsyncSession, data: PromoCodeCreate) -> PromoCodeResponse:
    code_upper = data.code.upper()
    existing = await db.execute(select(PromoCode).where(PromoCode.code == code_upper))
    if existing.scalar_one_or_none():
        raise ConflictError(f"Promo code '{code_upper}' already exists")

    promo_data = data.model_dump()
    promo_data["code"] = code_upper
    promo = PromoCode(**promo_data)
    db.add(promo)
    await db.flush()
    await db.refresh(promo)
    return PromoCodeResponse.model_validate(promo)


async def update(db: AsyncSession, code: str, data: PromoCodeUpdate) -> PromoCodeResponse:
    result = await db.execute(select(PromoCode).where(PromoCode.code == code.upper()))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(promo, key, val)

    await db.flush()
    await db.refresh(promo)
    return PromoCodeResponse.model_validate(promo)


async def delete(db: AsyncSession, code: str) -> None:
    result = await db.execute(select(PromoCode).where(PromoCode.code == code.upper()))
    promo = result.scalar_one_or_none()
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")
    await db.delete(promo)
