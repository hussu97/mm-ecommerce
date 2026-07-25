"""
Gift cards, loyalty points and house accounts.

All three are customer-held value, and all three follow the same rule: the
balance column is a cached projection and the transaction table is the truth.
Every mutation writes a ledger row carrying `balance_after`, so a disputed
balance can be walked back line by line.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.base import utcnow
from app.models.marketing import (
    GiftCard,
    GiftCardStatusEnum,
    GiftCardTransaction,
    GiftCardTransactionTypeEnum,
    HouseAccount,
    HouseAccountTransaction,
    HouseAccountTransactionTypeEnum,
    LoyaltyProgram,
    LoyaltyTransaction,
    LoyaltyTransactionTypeEnum,
)
from app.models.order import Order
from app.models.user import User

__all__ = [
    "adjust_house_account",
    "charge_house_account",
    "earn_loyalty",
    "gift_card_balance",
    "house_account_for",
    "issue_gift_card",
    "loyalty_balance",
    "redeem_gift_card",
    "redeem_loyalty",
    "top_up_gift_card",
]

CENT = Decimal("0.01")
GIFT_CARD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1


def _q(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT)


# ─── Gift cards ───────────────────────────────────────────────────────────────


def generate_gift_card_code(length: int = 12) -> str:
    raw = "".join(secrets.choice(GIFT_CARD_ALPHABET) for _ in range(length))
    # Grouped for readability when a cashier reads it off a card.
    return "-".join(raw[i : i + 4] for i in range(0, length, 4))


async def issue_gift_card(
    db: AsyncSession,
    *,
    amount: Decimal,
    issued_by: User,
    customer_id: uuid.UUID | None = None,
    expires_in_days: int | None = None,
    code: str | None = None,
    notes: str | None = None,
) -> GiftCard:
    value = _q(amount)
    if value <= 0:
        raise BadRequestError("A gift card must be issued with a positive value")

    resolved_code = (code or generate_gift_card_code()).upper()
    existing = (
        await db.execute(select(GiftCard).where(GiftCard.code == resolved_code))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"Gift card {resolved_code} already exists")

    card = GiftCard(
        code=resolved_code,
        initial_balance=value,
        balance=value,
        status=GiftCardStatusEnum.ACTIVE.value,
        customer_id=customer_id,
        issued_by_id=issued_by.id,
        issued_at=utcnow(),
        expires_at=(
            (utcnow() + timedelta(days=expires_in_days)).date()
            if expires_in_days
            else None
        ),
        notes=notes,
    )
    db.add(card)
    await db.flush()

    db.add(
        GiftCardTransaction(
            gift_card_id=card.id,
            type=GiftCardTransactionTypeEnum.ISSUE.value,
            amount=value,
            balance_after=value,
            user_id=issued_by.id,
            notes=notes,
        )
    )
    await db.flush()
    await db.refresh(card)
    return card


async def find_gift_card(db: AsyncSession, code: str) -> GiftCard:
    card = (
        await db.execute(select(GiftCard).where(GiftCard.code == code.upper().strip()))
    ).scalar_one_or_none()
    if card is None:
        raise NotFoundError("Gift card not found")
    return card


async def gift_card_balance(db: AsyncSession, card: GiftCard) -> Decimal:
    """Recompute from the ledger rather than trusting the cached column."""
    total = (
        await db.execute(
            select(func.coalesce(func.sum(GiftCardTransaction.amount), 0)).where(
                GiftCardTransaction.gift_card_id == card.id
            )
        )
    ).scalar_one()
    return _q(total)


async def _record_gift_card_movement(
    db: AsyncSession,
    *,
    card: GiftCard,
    type_: str,
    amount: Decimal,
    user: User | None,
    order: Order | None = None,
    notes: str | None = None,
) -> GiftCardTransaction:
    balance = _q(await gift_card_balance(db, card) + amount)
    transaction = GiftCardTransaction(
        gift_card_id=card.id,
        type=type_,
        amount=_q(amount),
        balance_after=balance,
        order_id=order.id if order else None,
        user_id=user.id if user else None,
        notes=notes,
    )
    db.add(transaction)

    card.balance = balance
    if balance <= 0 and card.status == GiftCardStatusEnum.ACTIVE.value:
        card.status = GiftCardStatusEnum.REDEEMED.value
    elif balance > 0 and card.status == GiftCardStatusEnum.REDEEMED.value:
        card.status = GiftCardStatusEnum.ACTIVE.value

    await db.flush()
    await db.refresh(transaction)
    return transaction


async def top_up_gift_card(
    db: AsyncSession, *, card: GiftCard, amount: Decimal, user: User
) -> GiftCardTransaction:
    if _q(amount) <= 0:
        raise BadRequestError("Top-up must be positive")
    if card.status == GiftCardStatusEnum.CANCELLED.value:
        raise ConflictError("This gift card has been cancelled")
    return await _record_gift_card_movement(
        db,
        card=card,
        type_=GiftCardTransactionTypeEnum.TOP_UP.value,
        amount=_q(amount),
        user=user,
    )


async def redeem_gift_card(
    db: AsyncSession,
    *,
    card: GiftCard,
    amount: Decimal,
    user: User,
    order: Order | None = None,
) -> GiftCardTransaction:
    """
    Spend against a card. Refuses to overdraw — the caller is expected to cap the
    redemption at the card's balance and settle the rest another way.
    """
    value = _q(amount)
    if value <= 0:
        raise BadRequestError("Redemption must be positive")
    if not card.is_usable:
        raise ConflictError(f"Gift card {card.code} is {card.status}")
    if card.expires_at is not None and card.expires_at < utcnow().date():
        card.status = GiftCardStatusEnum.EXPIRED.value
        await db.flush()
        raise ConflictError(f"Gift card {card.code} expired on {card.expires_at}")

    available = await gift_card_balance(db, card)
    if value > available:
        raise ConflictError(
            f"Gift card only has {available} available, cannot redeem {value}"
        )

    return await _record_gift_card_movement(
        db,
        card=card,
        type_=GiftCardTransactionTypeEnum.REDEEM.value,
        amount=-value,
        user=user,
        order=order,
    )


async def cancel_gift_card(
    db: AsyncSession, *, card: GiftCard, user: User, notes: str | None = None
) -> GiftCard:
    remaining = await gift_card_balance(db, card)
    if remaining > 0:
        await _record_gift_card_movement(
            db,
            card=card,
            type_=GiftCardTransactionTypeEnum.CANCEL.value,
            amount=-remaining,
            user=user,
            notes=notes,
        )
    card.status = GiftCardStatusEnum.CANCELLED.value
    await db.flush()
    await db.refresh(card)
    return card


# ─── Loyalty ──────────────────────────────────────────────────────────────────


async def active_program(db: AsyncSession) -> LoyaltyProgram | None:
    return (
        await db.execute(
            select(LoyaltyProgram).where(LoyaltyProgram.is_active.is_(True)).limit(1)
        )
    ).scalar_one_or_none()


async def loyalty_balance(db: AsyncSession, customer_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(LoyaltyTransaction.points), 0)).where(
                LoyaltyTransaction.customer_id == customer_id
            )
        )
    ).scalar_one()
    return int(total or 0)


async def _record_loyalty(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID,
    type_: str,
    points: int,
    amount: Decimal = Decimal("0"),
    order: Order | None = None,
    notes: str | None = None,
) -> LoyaltyTransaction:
    balance = await loyalty_balance(db, customer_id) + points
    transaction = LoyaltyTransaction(
        customer_id=customer_id,
        type=type_,
        points=points,
        balance_after=balance,
        amount=_q(amount),
        order_id=order.id if order else None,
        notes=notes,
    )
    db.add(transaction)
    await db.flush()
    await db.refresh(transaction)
    return transaction


async def earn_loyalty(db: AsyncSession, *, order: Order) -> LoyaltyTransaction | None:
    """
    Award points for a closed order. Returns None when there is no active
    programme or no identified customer, which is the common walk-in case.
    """
    if order.user_id is None:
        return None
    program = await active_program(db)
    if program is None:
        return None
    if program.branch_ids and order.branch_id not in program.branch_ids:
        return None

    already = (
        await db.execute(
            select(LoyaltyTransaction).where(
                LoyaltyTransaction.order_id == order.id,
                LoyaltyTransaction.type == LoyaltyTransactionTypeEnum.EARN.value,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return already  # earning is idempotent per order

    # Points are earned on the tax-exclusive net, so VAT is not rewarded.
    base = Decimal(str(order.total_excl_vat))
    if not program.earn_on_discounted_items:
        base -= Decimal(str(order.discount_amount))
    if base <= 0:
        return None

    points = int(
        (base * Decimal(str(program.points_per_currency_unit))).to_integral_value()
    )
    if points <= 0:
        return None

    return await _record_loyalty(
        db,
        customer_id=order.user_id,
        type_=LoyaltyTransactionTypeEnum.EARN.value,
        points=points,
        order=order,
        notes=f"Earned on {order.order_number}",
    )


async def redeem_loyalty(
    db: AsyncSession,
    *,
    customer_id: uuid.UUID,
    points: int,
    order: Order | None = None,
) -> tuple[LoyaltyTransaction, Decimal]:
    """Spend points. Returns the ledger row and the cash value released."""
    program = await active_program(db)
    if program is None:
        raise ConflictError("There is no active loyalty programme")
    if points <= 0:
        raise BadRequestError("Redemption must be a positive number of points")
    if points < program.minimum_points_to_redeem:
        raise ConflictError(
            f"At least {program.minimum_points_to_redeem} points are needed to redeem"
        )

    balance = await loyalty_balance(db, customer_id)
    if points > balance:
        raise ConflictError(f"Customer only has {balance} points")

    value = _q(Decimal(points) * Decimal(str(program.currency_per_point)))
    transaction = await _record_loyalty(
        db,
        customer_id=customer_id,
        type_=LoyaltyTransactionTypeEnum.REDEEM.value,
        points=-points,
        amount=value,
        order=order,
    )
    return transaction, value


# ─── House accounts ───────────────────────────────────────────────────────────


async def house_account_for(
    db: AsyncSession, customer_id: uuid.UUID
) -> HouseAccount | None:
    return (
        await db.execute(
            select(HouseAccount).where(HouseAccount.customer_id == customer_id)
        )
    ).scalar_one_or_none()


async def _record_house_account(
    db: AsyncSession,
    *,
    account: HouseAccount,
    type_: str,
    amount: Decimal,
    user: User | None,
    order: Order | None = None,
    notes: str | None = None,
) -> HouseAccountTransaction:
    balance = _q(Decimal(str(account.balance)) + amount)
    transaction = HouseAccountTransaction(
        house_account_id=account.id,
        type=type_,
        amount=_q(amount),
        balance_after=balance,
        order_id=order.id if order else None,
        user_id=user.id if user else None,
        notes=notes,
    )
    db.add(transaction)
    account.balance = balance
    await db.flush()
    await db.refresh(transaction)
    return transaction


async def charge_house_account(
    db: AsyncSession,
    *,
    account: HouseAccount,
    amount: Decimal,
    user: User,
    order: Order | None = None,
) -> HouseAccountTransaction:
    """Sign an order to the account, refusing to exceed the credit limit."""
    value = _q(amount)
    if value <= 0:
        raise BadRequestError("Charge must be positive")
    if not account.is_active:
        raise ConflictError("This house account is not active")
    if value > account.available_credit:
        raise ConflictError(
            f"Charge of {value} exceeds the {account.available_credit} credit remaining"
        )
    return await _record_house_account(
        db,
        account=account,
        type_=HouseAccountTransactionTypeEnum.CHARGE.value,
        amount=value,
        user=user,
        order=order,
    )


async def settle_house_account(
    db: AsyncSession,
    *,
    account: HouseAccount,
    amount: Decimal,
    user: User,
    notes: str | None = None,
) -> HouseAccountTransaction:
    value = _q(amount)
    if value <= 0:
        raise BadRequestError("Payment must be positive")
    return await _record_house_account(
        db,
        account=account,
        type_=HouseAccountTransactionTypeEnum.PAYMENT.value,
        amount=-value,
        user=user,
        notes=notes,
    )


async def adjust_house_account(
    db: AsyncSession,
    *,
    account: HouseAccount,
    amount: Decimal,
    user: User,
    notes: str | None = None,
) -> HouseAccountTransaction:
    """Signed correction — positive increases what is owed."""
    return await _record_house_account(
        db,
        account=account,
        type_=HouseAccountTransactionTypeEnum.ADJUSTMENT.value,
        amount=_q(amount),
        user=user,
        notes=notes,
    )
