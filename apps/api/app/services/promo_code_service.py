from __future__ import annotations

import secrets
import uuid

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.phone import phone_identities
from app.models.order import Order, OrderStatusEnum
from app.models.promo_code import DiscountTypeEnum, PromoCode
from app.services import firebase_auth_service
from app.schemas.promo_code import (
    PromoCodeAdvertResponse,
    PromoCodeCreate,
    PromoCodeResponse,
    PromoCodeUpdate,
    PromoCodeValidateResponse,
)

__all__ = [
    "advertisable",
    "create",
    "delete",
    "get_all",
    "get_promo",
    "update",
    "validate",
]


def _calc_discount(promo: PromoCode, subtotal: Decimal) -> Decimal:
    if promo.discount_type == DiscountTypeEnum.PERCENTAGE:
        amount = (subtotal * promo.discount_value / Decimal("100")).quantize(
            Decimal("0.01")
        )
    else:
        amount = min(promo.discount_value, subtotal)
    # A percentage scales with the basket, which is fine at 60 dirhams and not
    # fine at 600. The cap is what makes "15% off" safe to advertise nationally.
    if promo.max_discount_amount is not None:
        amount = min(amount, Decimal(str(promo.max_discount_amount)))
    # A discount can take an order to zero and no further. The percentage is
    # capped at 100 on the way in, but a row that predates that check — or one
    # written straight to the database — would otherwise produce a discount
    # larger than the basket, and from there a negative subtotal, negative VAT,
    # and a total that `create_session` reads as "zero, nothing to charge" and
    # confirms for free.
    return max(Decimal("0.00"), min(amount, subtotal))


def normalise_code(code: str) -> str:
    """
    What the customer typed, reduced to what we match on.

    `.upper()` alone was the rule and it left whitespace in: a pasted code with
    a trailing space matched nothing, and the storefront's own `.trim()` was the
    only thing hiding it. The API is a second entry point and cannot rely on a
    client being polite.
    """
    return (code or "").strip().upper()


async def find_by_code(db: AsyncSession, code: str) -> PromoCode | None:
    """
    One coupon, whichever language it was typed in.

    The Arabic spelling is a second name for the same row, not a second coupon,
    so it resolves here and every limit downstream — campaign ceiling, per-user
    ceiling, first-orders rule — counts both together. Anything else would let a
    customer redeem the English code and then the Arabic one.
    """
    wanted = normalise_code(code)
    if not wanted:
        return None
    result = await db.execute(
        select(PromoCode).where(
            or_(PromoCode.code == wanted, PromoCode.code_ar == wanted)
        )
    )
    return result.scalar_one_or_none()


async def orders_placed_by(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    email: str | None,
    phone: str | None,
) -> int:
    """
    How many orders this person has placed, under any of the names we know them by.

    Three identities, OR'd, because no single one of them holds. The account is
    useless on its own: guest checkout mints a fresh `users` row per session, so
    keying on `user_id` means every guest is a new customer forever. Email is
    better but a guest without one gets a generated `…@guest.local`. Phone is the
    one people reuse and the one they verify, and it is on the order because
    somebody has to be called when the driver cannot find the door.

    Cancelled orders do not count — an order that never happened is not a
    purchase. Everything else does, including orders still in flight. Counting
    only *delivered* ones would be the more literal reading of "first three
    orders", and it would let somebody place ten in one evening before any of
    them lands.

    The phone is matched on its canonical E.164 form **and** on the string as it
    arrived. Without the first, `0501234567` and `+971501234567` are two
    customers and the rule is bypassed by retyping one's own number differently
    — the storefront's `PhoneInput` always sends E.164, but this API is public
    and a scripted client is not obliged to be. Without the second, rows written
    at the counter before this normalisation existed stop being seen, and a
    customer's history silently resets.
    """
    identities = []
    if user_id is not None:
        identities.append(Order.user_id == user_id)
    if email:
        identities.append(func.lower(Order.email) == email.strip().lower())
    spellings = phone_identities(phone)
    if spellings:
        identities.append(Order.customer_phone.in_(spellings))
    if not identities:
        return 0

    result = await db.execute(
        select(func.count())
        .select_from(Order)
        .where(or_(*identities), Order.status != OrderStatusEnum.CANCELLED)
    )
    return int(result.scalar() or 0)


async def _redemptions_by(
    db: AsyncSession, promo: PromoCode, user_id: uuid.UUID
) -> int:
    """
    How many orders this customer has already placed with this code.

    Counted off `orders.promo_code_used` rather than a separate ledger, because
    that column is written in the same transaction as the order and is
    therefore exactly as true as the order itself. Cancelled orders are
    excluded — a code spent on an order that never happened has not been spent.
    """
    codes = [c for c in (promo.code, promo.code_ar) if c]
    result = await db.execute(
        select(func.count())
        .select_from(Order)
        .where(
            Order.user_id == user_id,
            Order.promo_code_used.in_(codes),
            Order.status != OrderStatusEnum.CANCELLED,
        )
    )
    return int(result.scalar() or 0)


async def validate(
    db: AsyncSession,
    code: str,
    subtotal: Decimal,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> PromoCodeValidateResponse:
    """Validate a promo code and return the discount amount (does NOT increment uses)."""
    promo = await find_by_code(db, code)

    if not promo:
        return PromoCodeValidateResponse(valid=False, message="Promo code not found")

    if not promo.is_active:
        return PromoCodeValidateResponse(
            valid=False, message="Promo code is not active"
        )

    now = datetime.now(timezone.utc)
    if promo.valid_from and now < promo.valid_from:
        return PromoCodeValidateResponse(
            valid=False, message="Promo code is not yet valid"
        )

    if promo.valid_until and now > promo.valid_until:
        return PromoCodeValidateResponse(valid=False, message="Promo code has expired")

    if promo.max_uses is not None and promo.current_uses >= promo.max_uses:
        return PromoCodeValidateResponse(
            valid=False, message="Promo code has reached its usage limit"
        )

    if getattr(promo, "requires_phone_verification", False):
        verified = await firebase_auth_service.is_phone_verified(db, phone)
        if not verified:
            return PromoCodeValidateResponse(
                valid=False,
                # Named plainly, because unlike the new-customer refusal this
                # one is actionable: there is a button that fixes it.
                message="Verify your phone number to use this code",
                requires_phone_verification=True,
            )

    if promo.first_orders_limit is not None:
        placed = await orders_placed_by(db, user_id=user_id, email=email, phone=phone)
        if placed >= promo.first_orders_limit:
            return PromoCodeValidateResponse(
                valid=False,
                # Says what is true without saying how it was worked out. The
                # customer does not need to know we matched them on a phone
                # number, and telling them would be a hint about how to avoid it.
                message="This code is for new customers only",
            )

    if promo.max_uses_per_user is not None and user_id is not None:
        used = await _redemptions_by(db, promo, user_id)
        if used >= promo.max_uses_per_user:
            return PromoCodeValidateResponse(
                valid=False,
                message="You have already used this promo code",
            )

    if promo.min_order_amount and subtotal < promo.min_order_amount:
        return PromoCodeValidateResponse(
            valid=False,
            message=f"Minimum order amount of {promo.min_order_amount} AED required",
        )

    discount = _calc_discount(promo, subtotal)
    return PromoCodeValidateResponse(valid=True, discount_amount=discount)


async def advertisable(db: AsyncSession) -> PromoCodeAdvertResponse | None:
    """
    The acquisition coupon the storefront may put in front of a shopper, if there
    is one running.

    "Acquisition" is `first_orders_limit IS NOT NULL`, which is the column that
    makes a code a new-customer offer rather than a private one. A bulk-issued
    single-use coupon, a partner code, a customer-service goodwill code — none
    of them carry it, and none of them are safe to print on a public page.

    Filtered here rather than in the caller so there is exactly one definition of
    "currently advertisable", and so a code whose window has closed or whose
    campaign ceiling is spent stops being advertised the moment it does. Showing
    a tray for a coupon that `validate` will refuse is worse than showing no tray
    at all: the customer reads an offer, taps it, and is told no.

    Newest first, so publishing a replacement campaign is enough to retire the
    one before it — an admin should not have to remember to deactivate the old
    row for the storefront to move on. `None` when nothing qualifies, which the
    storefront renders as no tray rather than as an error.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(PromoCode)
        .where(
            PromoCode.is_active.is_(True),
            PromoCode.first_orders_limit.is_not(None),
            or_(PromoCode.valid_from.is_(None), PromoCode.valid_from <= now),
            or_(PromoCode.valid_until.is_(None), PromoCode.valid_until >= now),
            or_(
                PromoCode.max_uses.is_(None),
                PromoCode.current_uses < PromoCode.max_uses,
            ),
        )
        .order_by(PromoCode.created_at.desc())
        .limit(1)
    )
    promo = (await db.execute(stmt)).scalars().first()
    return PromoCodeAdvertResponse.model_validate(promo) if promo else None


async def get_promo(db: AsyncSession, code: str) -> PromoCode:
    """Fetch and validate a promo for use during order creation. Raises on invalid."""
    promo = await find_by_code(db, code)
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")
    return promo


async def get_all(
    db: AsyncSession, include_inactive: bool = False
) -> list[PromoCodeResponse]:
    stmt = select(PromoCode).order_by(PromoCode.created_at.desc())
    if not include_inactive:
        stmt = stmt.where(PromoCode.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    return [PromoCodeResponse.model_validate(p) for p in result.scalars().all()]


async def create(db: AsyncSession, data: PromoCodeCreate) -> PromoCodeResponse:
    code_upper = normalise_code(data.code)
    code_ar = normalise_code(data.code_ar) or None

    # Both spellings share one namespace. An Arabic code that collides with
    # somebody else's English one would make `find_by_code` ambiguous, and the
    # unique indexes would refuse the insert anyway — with a constraint error
    # rather than something an admin can read.
    for candidate in (c for c in (code_upper, code_ar) if c):
        clash = await find_by_code(db, candidate)
        if clash is not None:
            raise ConflictError(f"Promo code '{candidate}' already exists")

    promo_data = data.model_dump()
    promo_data["code"] = code_upper
    promo_data["code_ar"] = code_ar
    promo = PromoCode(**promo_data)
    db.add(promo)
    await db.flush()
    await db.refresh(promo)
    return PromoCodeResponse.model_validate(promo)


async def update(
    db: AsyncSession, code: str, data: PromoCodeUpdate
) -> PromoCodeResponse:
    promo = await find_by_code(db, code)
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(promo, key, val)

    await db.flush()
    await db.refresh(promo)
    return PromoCodeResponse.model_validate(promo)


async def delete(db: AsyncSession, code: str) -> None:
    promo = await find_by_code(db, code)
    if not promo:
        raise NotFoundError(f"Promo code '{code}' not found")
    promo.is_active = False
    await db.flush()


#: Ambiguous characters are left out: a customer reads these off a printed
#: card or a phone screen, and O/0 and I/1 generate support calls.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


async def create_bulk(db: AsyncSession, data) -> list[str]:
    """
    Generate `count` unique coupon codes sharing a prefix.

    Uniqueness is checked against the database rather than trusted to
    randomness, because a collision would silently hand two customers the
    same single-use code and the second would be told it was already spent.
    """
    existing = set(
        (
            await db.execute(
                select(PromoCode.code).where(PromoCode.code.like(f"{data.prefix}-%"))
            )
        )
        .scalars()
        .all()
    )

    created: list[str] = []
    attempts = 0
    max_attempts = data.count * 20
    while len(created) < data.count and attempts < max_attempts:
        attempts += 1
        suffix = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        code = f"{data.prefix}-{suffix}"
        if code in existing:
            continue
        existing.add(code)
        db.add(
            PromoCode(
                code=code,
                discount_type=data.discount_type,
                discount_value=data.discount_value,
                min_order_amount=data.min_order_amount,
                max_uses=data.max_uses,
                # Every limit the caller asked for, not just the ones that were
                # remembered. `max_uses_per_user` was accepted by the schema and
                # silently dropped here, so a bulk campaign that meant
                # "one redemption each" generated codes with no per-customer
                # ceiling at all.
                max_uses_per_user=getattr(data, "max_uses_per_user", None),
                max_discount_amount=getattr(data, "max_discount_amount", None),
                first_orders_limit=getattr(data, "first_orders_limit", None),
                current_uses=0,
                is_active=True,
                valid_from=data.valid_from,
                valid_until=data.valid_until,
            )
        )
        created.append(code)

    if len(created) < data.count:
        raise ConflictError(
            f"Could only generate {len(created)} of {data.count} unique codes; "
            "try a different prefix"
        )

    await db.flush()
    return created
