"""Discounts, promotions, timed events, gift cards, loyalty and house accounts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user, get_db
from app.core.exceptions import ForbiddenError
from app.models import (
    Discount,
    GiftCard,
    GiftCardTransaction,
    HouseAccount,
    LoyaltyProgram,
    LoyaltyTransaction,
    Promotion,
    TimedEvent,
)
from app.models.user import User
from app.services import crud_service, marketing_service

from .pos_config import build_crud_router

Translations = dict[str, dict[str, str]]
OrderTypeLiteral = Literal["dine_in", "pickup", "delivery", "drive_thru"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _require(user: User, permission: str) -> None:
    if not user.can(permission):
        raise ForbiddenError(f"You do not have permission to {permission}")


# ─── Discounts ────────────────────────────────────────────────────────────────


class DiscountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    qualification: Literal["product", "order", "both"] = "order"
    amount: Decimal = Field(Decimal("0"), ge=0)
    is_percentage: bool = True
    is_taxable: bool = True
    minimum_order_price: Decimal = Field(Decimal("0"), ge=0)
    minimum_product_price: Decimal = Field(Decimal("0"), ge=0)
    maximum_amount: Decimal | None = Field(None, ge=0)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    is_active: bool = True


class DiscountUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    qualification: Literal["product", "order", "both"] | None = None
    amount: Decimal | None = Field(None, ge=0)
    is_percentage: bool | None = None
    is_taxable: bool | None = None
    minimum_order_price: Decimal | None = Field(None, ge=0)
    minimum_product_price: Decimal | None = Field(None, ge=0)
    maximum_amount: Decimal | None = Field(None, ge=0)
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    is_active: bool | None = None


class DiscountResponse(ORMModel):
    id: uuid.UUID
    name: str
    name_localized: str | None
    reference: str | None
    qualification: str
    amount: Decimal
    is_percentage: bool
    is_taxable: bool
    minimum_order_price: Decimal
    minimum_product_price: Decimal
    maximum_amount: Decimal | None
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


discounts_router = build_crud_router(
    model=Discount,
    create_schema=DiscountCreate,
    update_schema=DiscountUpdate,
    response_schema=DiscountResponse,
    entity_type="discount",
)


# ─── Promotions ───────────────────────────────────────────────────────────────


class ScheduleFields(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    from_time: int = Field(0, ge=0, le=1439)
    to_time: int = Field(1439, ge=0, le=1439)
    is_mon: bool = True
    is_tue: bool = True
    is_wed: bool = True
    is_thu: bool = True
    is_fri: bool = True
    is_sat: bool = True
    is_sun: bool = True


class PromotionCreate(ScheduleFields):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: Literal["basic", "advanced"] = "basic"
    trigger: Literal["quantity", "spend"] = "spend"
    trigger_value: Decimal = Field(Decimal("0"), ge=0)
    reward: Literal[
        "percentage_off_products",
        "fixed_off_products",
        "percentage_off_order",
        "fixed_off_order",
        "fixed_price",
        "free_product",
    ]
    reward_value: Decimal = Field(Decimal("0"), ge=0)
    trigger_product_ids: list[uuid.UUID] = Field(default_factory=list)
    reward_product_ids: list[uuid.UUID] = Field(default_factory=list)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    customer_tag_ids: list[uuid.UUID] = Field(default_factory=list)
    priority: int = Field(100, ge=0, le=10000)
    max_uses_per_order: int = Field(1, ge=1, le=100)
    is_active: bool = True


class PromotionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    type: Literal["basic", "advanced"] | None = None
    trigger: Literal["quantity", "spend"] | None = None
    trigger_value: Decimal | None = Field(None, ge=0)
    reward: str | None = None
    reward_value: Decimal | None = Field(None, ge=0)
    trigger_product_ids: list[uuid.UUID] | None = None
    reward_product_ids: list[uuid.UUID] | None = None
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    max_uses_per_order: int | None = Field(None, ge=1, le=100)
    from_date: date | None = None
    to_date: date | None = None
    from_time: int | None = Field(None, ge=0, le=1439)
    to_time: int | None = Field(None, ge=0, le=1439)
    is_mon: bool | None = None
    is_tue: bool | None = None
    is_wed: bool | None = None
    is_thu: bool | None = None
    is_fri: bool | None = None
    is_sat: bool | None = None
    is_sun: bool | None = None
    is_active: bool | None = None


class PromotionResponse(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    trigger: str
    trigger_value: Decimal
    reward: str
    reward_value: Decimal
    trigger_product_ids: list[uuid.UUID]
    reward_product_ids: list[uuid.UUID]
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    priority: int
    max_uses_per_order: int
    from_date: date | None
    to_date: date | None
    from_time: int
    to_time: int
    is_mon: bool
    is_tue: bool
    is_wed: bool
    is_thu: bool
    is_fri: bool
    is_sat: bool
    is_sun: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


promotions_router = build_crud_router(
    model=Promotion,
    create_schema=PromotionCreate,
    update_schema=PromotionUpdate,
    response_schema=PromotionResponse,
    entity_type="promotion",
)


# ─── Timed events ─────────────────────────────────────────────────────────────


class TimedEventCreate(ScheduleFields):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: Literal["percentage", "fixed", "fixed_price"]
    value: Decimal = Field(Decimal("0"), ge=0)
    product_ids: list[uuid.UUID] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list)
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    priority: int = Field(100, ge=0, le=10000)
    is_active: bool = True


class TimedEventUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    type: Literal["percentage", "fixed", "fixed_price"] | None = None
    value: Decimal | None = Field(None, ge=0)
    product_ids: list[uuid.UUID] | None = None
    category_ids: list[uuid.UUID] | None = None
    branch_ids: list[uuid.UUID] | None = None
    order_types: list[OrderTypeLiteral] | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    from_date: date | None = None
    to_date: date | None = None
    from_time: int | None = Field(None, ge=0, le=1439)
    to_time: int | None = Field(None, ge=0, le=1439)
    is_mon: bool | None = None
    is_tue: bool | None = None
    is_wed: bool | None = None
    is_thu: bool | None = None
    is_fri: bool | None = None
    is_sat: bool | None = None
    is_sun: bool | None = None
    is_active: bool | None = None


class TimedEventResponse(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    value: Decimal
    product_ids: list[uuid.UUID]
    category_ids: list[uuid.UUID]
    branch_ids: list[uuid.UUID]
    order_types: list[str]
    priority: int
    from_date: date | None
    to_date: date | None
    from_time: int
    to_time: int
    is_mon: bool
    is_tue: bool
    is_wed: bool
    is_thu: bool
    is_fri: bool
    is_sat: bool
    is_sun: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


timed_events_router = build_crud_router(
    model=TimedEvent,
    create_schema=TimedEventCreate,
    update_schema=TimedEventUpdate,
    response_schema=TimedEventResponse,
    entity_type="timed_event",
)


# ─── Gift cards ───────────────────────────────────────────────────────────────

gift_cards_router = APIRouter()


class GiftCardIssue(BaseModel):
    amount: Decimal = Field(gt=0)
    customer_id: uuid.UUID | None = None
    expires_in_days: int | None = Field(None, ge=1, le=3650)
    code: str | None = Field(None, max_length=40)
    notes: str | None = None


class GiftCardAmount(BaseModel):
    amount: Decimal = Field(gt=0)
    notes: str | None = None


class GiftCardTransactionResponse(ORMModel):
    id: uuid.UUID
    type: str
    amount: Decimal
    balance_after: Decimal
    order_id: uuid.UUID | None
    notes: str | None
    created_at: datetime


class GiftCardResponse(ORMModel):
    id: uuid.UUID
    code: str
    initial_balance: Decimal
    balance: Decimal
    status: str
    customer_id: uuid.UUID | None
    issued_at: datetime
    expires_at: date | None
    notes: str | None
    transactions: list[GiftCardTransactionResponse] = []


@gift_cards_router.get("", response_model=list[GiftCardResponse])
async def list_gift_cards(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.gift_cards.manage")
    stmt = select(GiftCard)
    if status_filter:
        stmt = stmt.where(GiftCard.status == status_filter)
    stmt = stmt.order_by(GiftCard.issued_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().unique().all())


@gift_cards_router.post(
    "", response_model=GiftCardResponse, status_code=status.HTTP_201_CREATED
)
async def issue_gift_card(
    data: GiftCardIssue,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.gift_cards.manage")
    return await marketing_service.issue_gift_card(
        db,
        amount=data.amount,
        issued_by=user,
        customer_id=data.customer_id,
        expires_in_days=data.expires_in_days,
        code=data.code,
        notes=data.notes,
    )


@gift_cards_router.get("/lookup/{code}", response_model=GiftCardResponse)
async def lookup_gift_card(
    code: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Used by the terminal before accepting a gift card as tender."""
    _require(user, "pos.payment.perform")
    return await marketing_service.find_gift_card(db, code)


@gift_cards_router.post("/{card_id}/top-up", response_model=GiftCardResponse)
async def top_up(
    card_id: uuid.UUID,
    data: GiftCardAmount,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.gift_cards.manage")
    card = await crud_service.get_or_404(db, GiftCard, card_id)
    await marketing_service.top_up_gift_card(
        db, card=card, amount=data.amount, user=user
    )
    return await crud_service.get_or_404(db, GiftCard, card_id)


@gift_cards_router.post("/{card_id}/cancel", response_model=GiftCardResponse)
async def cancel(
    card_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.gift_cards.manage")
    card = await crud_service.get_or_404(db, GiftCard, card_id)
    return await marketing_service.cancel_gift_card(db, card=card, user=user)


@gift_cards_router.get(
    "/{card_id}/transactions", response_model=list[GiftCardTransactionResponse]
)
async def gift_card_transactions(
    card_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.gift_cards.manage")
    stmt = (
        select(GiftCardTransaction)
        .where(GiftCardTransaction.gift_card_id == card_id)
        .order_by(GiftCardTransaction.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


# ─── Loyalty ──────────────────────────────────────────────────────────────────

loyalty_router = APIRouter()


class LoyaltyProgramUpdate(BaseModel):
    name: str | None = Field(None, max_length=150)
    points_per_currency_unit: Decimal | None = Field(None, ge=0)
    currency_per_point: Decimal | None = Field(None, ge=0)
    minimum_points_to_redeem: int | None = Field(None, ge=0)
    points_expire_after_days: int | None = Field(None, ge=0)
    earn_on_discounted_items: bool | None = None
    branch_ids: list[uuid.UUID] | None = None
    is_active: bool | None = None


class LoyaltyProgramResponse(ORMModel):
    id: uuid.UUID
    name: str
    points_per_currency_unit: Decimal
    currency_per_point: Decimal
    minimum_points_to_redeem: int
    points_expire_after_days: int
    earn_on_discounted_items: bool
    branch_ids: list[uuid.UUID]
    is_active: bool


class LoyaltyBalanceResponse(BaseModel):
    customer_id: uuid.UUID
    points: int
    value: Decimal


class LoyaltyRedeem(BaseModel):
    customer_id: uuid.UUID
    points: int = Field(gt=0)
    order_id: uuid.UUID | None = None


@loyalty_router.get("/program", response_model=LoyaltyProgramResponse)
async def get_program(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Created lazily so a fresh deployment needs no seed step."""
    program = (await db.execute(select(LoyaltyProgram).limit(1))).scalar_one_or_none()
    if program is None:
        program = LoyaltyProgram()
        db.add(program)
        await db.flush()
        await db.refresh(program)
    return program


@loyalty_router.put("/program", response_model=LoyaltyProgramResponse)
async def update_program(
    data: LoyaltyProgramUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "admin.loyalty.manage")
    program = (await db.execute(select(LoyaltyProgram).limit(1))).scalar_one_or_none()
    if program is None:
        program = LoyaltyProgram()
        db.add(program)
        await db.flush()
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(program, field, value)
    await db.flush()
    await db.refresh(program)
    return program


@loyalty_router.get("/balance/{customer_id}", response_model=LoyaltyBalanceResponse)
async def loyalty_balance(
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "customers.insights.read")
    points = await marketing_service.loyalty_balance(db, customer_id)
    program = await marketing_service.active_program(db)
    rate = Decimal(str(program.currency_per_point)) if program else Decimal("0")
    return LoyaltyBalanceResponse(
        customer_id=customer_id,
        points=points,
        value=(Decimal(points) * rate).quantize(Decimal("0.01")),
    )


@loyalty_router.post("/redeem", response_model=LoyaltyBalanceResponse)
async def redeem_points(
    data: LoyaltyRedeem,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "customers.loyalty.manage")
    _, value = await marketing_service.redeem_loyalty(
        db, customer_id=data.customer_id, points=data.points
    )
    remaining = await marketing_service.loyalty_balance(db, data.customer_id)
    return LoyaltyBalanceResponse(
        customer_id=data.customer_id, points=remaining, value=value
    )


@loyalty_router.get("/transactions/{customer_id}")
async def loyalty_transactions(
    customer_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "customers.insights.read")
    stmt = (
        select(LoyaltyTransaction)
        .where(LoyaltyTransaction.customer_id == customer_id)
        .order_by(LoyaltyTransaction.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


# ─── House accounts ───────────────────────────────────────────────────────────

house_accounts_router = APIRouter()


class HouseAccountUpsert(BaseModel):
    customer_id: uuid.UUID
    credit_limit: Decimal = Field(Decimal("0"), ge=0)
    is_active: bool = True
    notes: str | None = None


class HouseAccountAmount(BaseModel):
    amount: Decimal
    notes: str | None = None


class HouseAccountResponse(ORMModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    credit_limit: Decimal
    balance: Decimal
    available_credit: Decimal
    is_active: bool
    notes: str | None


@house_accounts_router.get("", response_model=list[HouseAccountResponse])
async def list_house_accounts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "customers.house_account.manage")
    stmt = select(HouseAccount).order_by(HouseAccount.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


@house_accounts_router.post(
    "", response_model=HouseAccountResponse, status_code=status.HTTP_201_CREATED
)
async def create_house_account(
    data: HouseAccountUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "customers.house_account.manage")
    existing = await marketing_service.house_account_for(db, data.customer_id)
    if existing is not None:
        return await crud_service.update(db, existing, data)
    return await crud_service.create(db, HouseAccount, data)


@house_accounts_router.post("/{account_id}/settle", response_model=HouseAccountResponse)
async def settle(
    account_id: uuid.UUID,
    data: HouseAccountAmount,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Record a payment from the customer against their outstanding balance."""
    _require(user, "customers.house_account.manage")
    account = await crud_service.get_or_404(db, HouseAccount, account_id)
    await marketing_service.settle_house_account(
        db, account=account, amount=data.amount, user=user, notes=data.notes
    )
    return await crud_service.get_or_404(db, HouseAccount, account_id)


@house_accounts_router.post("/{account_id}/adjust", response_model=HouseAccountResponse)
async def adjust(
    account_id: uuid.UUID,
    data: HouseAccountAmount,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Signed correction — positive increases what the customer owes."""
    _require(user, "customers.house_account.manage")
    account = await crud_service.get_or_404(db, HouseAccount, account_id)
    await marketing_service.adjust_house_account(
        db, account=account, amount=data.amount, user=user, notes=data.notes
    )
    return await crud_service.get_or_404(db, HouseAccount, account_id)


@house_accounts_router.get("/{account_id}/transactions")
async def house_account_transactions(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    from app.models import HouseAccountTransaction

    _require(user, "customers.house_account.manage")
    stmt = (
        select(HouseAccountTransaction)
        .where(HouseAccountTransaction.house_account_id == account_id)
        .order_by(HouseAccountTransaction.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


__all__ = [
    "discounts_router",
    "gift_cards_router",
    "house_accounts_router",
    "loyalty_router",
    "promotions_router",
    "timed_events_router",
]
