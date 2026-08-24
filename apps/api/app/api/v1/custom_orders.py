"""
The custom-cake diary.

Two audiences on one calendar. The storefront asks which dates are still open so
a customer cannot pick a full Saturday, and the admin reads and writes the whole
book — including the Instagram and WhatsApp orders that never become website
orders and would otherwise be invisible to it.

The public half deliberately answers only "how many are left", never who booked
them: availability is not a customer list.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.limiter import limiter
from app.core.permissions import require
from app.models.custom_order import (
    CustomOrder,
    CustomOrderBlackout,
    CustomOrderSourceEnum,
    CustomOrderStatusEnum,
)
from app.models.user import User
from app.services import audit_service, custom_order_service
from app.services.pos import business_day_service

router = APIRouter()
admin_router = APIRouter()

#: How much of the calendar one request may ask for. A year is plenty for a
#: date picker and stops an open-ended range being a cheap way to make the
#: database build a million rows.
MAX_RANGE_DAYS = 400


# ─── Public: what a date picker needs ─────────────────────────────────────────


class DayAvailabilityResponse(BaseModel):
    date: date
    available: bool
    remaining: int
    #: Present only when the date is closed outright, so the picker can say why
    #: rather than showing a silently disabled day.
    reason: str | None = None


class AvailabilityResponse(BaseModel):
    earliest_date: date
    latest_date: date
    days: list[DayAvailabilityResponse]


@router.get("/availability", response_model=AvailabilityResponse)
@limiter.limit("60/minute")
async def get_availability(
    request: Request,
    product_id: uuid.UUID | None = Query(
        None, description="Applies this product's own lead time, if it has one"
    ),
    days: int = Query(90, ge=1, le=MAX_RANGE_DAYS),
    db: AsyncSession = Depends(get_db),
):
    """
    Which dates can still take a custom order.

    Public, because the date picker is on the product page and the customer has
    no account yet. It reports counts and never names — a customer asking when
    they can order a cake has no business learning who else ordered one.
    """
    from app.models.product import Product

    product = await db.get(Product, product_id) if product_id else None
    earliest = await custom_order_service.earliest_bookable_date(db, product)
    latest = earliest + timedelta(days=days - 1)

    availability = await custom_order_service.availability_range(db, earliest, latest)
    return AvailabilityResponse(
        earliest_date=earliest,
        latest_date=latest,
        days=[
            DayAvailabilityResponse(
                date=day.day,
                available=day.is_available,
                remaining=day.remaining,
                reason=day.blackout_reason if day.is_blackout else None,
            )
            for day in availability
        ],
    )


# ─── Admin: the whole book ────────────────────────────────────────────────────


class CustomOrderCreate(BaseModel):
    due_date: date
    customer_name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1)
    source: str = CustomOrderSourceEnum.INSTAGRAM.value
    status: str = CustomOrderStatusEnum.ENQUIRY.value
    customer_phone: str | None = Field(None, max_length=30)
    customer_email: str | None = Field(None, max_length=255)
    cake_message: str | None = Field(None, max_length=200)
    flavour: str | None = Field(None, max_length=120)
    size_label: str | None = Field(None, max_length=60)
    servings: int | None = Field(None, ge=1)
    reference_image_urls: list[str] = Field(default_factory=list)
    quoted_total: Decimal | None = Field(None, ge=0)
    deposit_amount: Decimal = Field(Decimal("0"), ge=0)
    branch_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    brief: dict = Field(default_factory=dict)
    admin_notes: str | None = None
    #: An Instagram order is often typed in after the fact, and refusing to
    #: record something that already happened would push it back into the chat
    #: thread this calendar replaces. Capacity is still enforced.
    allow_past: bool = False


class CustomOrderUpdate(BaseModel):
    due_date: date | None = None
    customer_name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, min_length=1)
    customer_phone: str | None = Field(None, max_length=30)
    customer_email: str | None = Field(None, max_length=255)
    cake_message: str | None = Field(None, max_length=200)
    flavour: str | None = Field(None, max_length=120)
    size_label: str | None = Field(None, max_length=60)
    servings: int | None = Field(None, ge=1)
    reference_image_urls: list[str] | None = None
    quoted_total: Decimal | None = Field(None, ge=0)
    deposit_amount: Decimal | None = Field(None, ge=0)
    branch_id: uuid.UUID | None = None
    admin_notes: str | None = None


class CustomOrderStatusUpdate(BaseModel):
    status: str


class CustomOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    due_date: date
    status: str
    source: str
    order_id: uuid.UUID | None
    customer_name: str
    customer_phone: str | None
    customer_email: str | None
    description: str
    cake_message: str | None
    flavour: str | None
    size_label: str | None
    servings: int | None
    reference_image_urls: list[str]
    quoted_total: Decimal | None
    deposit_amount: Decimal
    branch_id: uuid.UUID | None
    product_id: uuid.UUID | None
    brief: dict
    admin_notes: str | None


class CalendarDay(BaseModel):
    date: date
    capacity: int
    booked: int
    remaining: int
    is_blackout: bool
    blackout_reason: str | None
    orders: list[CustomOrderResponse]


@admin_router.get("/calendar", response_model=list[CalendarDay])
async def get_calendar(
    start: date,
    end: date,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    """The month view: every date in the window with what is booked on it."""
    if (end - start).days >= MAX_RANGE_DAYS:
        raise BadRequestError(f"Ask for at most {MAX_RANGE_DAYS} days at a time")

    availability = await custom_order_service.availability_range(db, start, end)
    rows = (
        (
            await db.execute(
                select(CustomOrder)
                .where(CustomOrder.due_date >= start, CustomOrder.due_date <= end)
                .order_by(CustomOrder.due_date, CustomOrder.created_at)
            )
        )
        .scalars()
        .all()
    )
    by_day: dict[date, list[CustomOrder]] = {}
    for row in rows:
        by_day.setdefault(row.due_date, []).append(row)

    return [
        CalendarDay(
            date=day.day,
            capacity=day.capacity,
            booked=day.booked,
            remaining=day.remaining,
            is_blackout=day.is_blackout,
            blackout_reason=day.blackout_reason,
            orders=[
                CustomOrderResponse.model_validate(row)
                for row in by_day.get(day.day, [])
            ],
        )
        for day in availability
    ]


@admin_router.post(
    "", response_model=CustomOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_custom_order(
    request: Request,
    data: CustomOrderCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.custom.manage")),
):
    """Record an order that arrived somewhere this system cannot see."""
    payload = data.model_dump(exclude={"allow_past"})
    custom_order = await custom_order_service.book(
        db, created_by_id=admin.id, allow_past=data.allow_past, **payload
    )
    await audit_service.log_action(
        db,
        user=admin,
        action="create",
        entity_type="custom_order",
        entity_id=str(custom_order.id),
        changes={"due_date": data.due_date.isoformat(), "source": data.source},
        request=request,
    )
    return CustomOrderResponse.model_validate(custom_order)


@admin_router.get("", response_model=list[CustomOrderResponse])
async def list_custom_orders(
    status_filter: str | None = Query(None, alias="status"),
    upcoming_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    stmt = select(CustomOrder)
    if status_filter:
        stmt = stmt.where(CustomOrder.status == status_filter)
    if upcoming_only:
        stmt = stmt.where(CustomOrder.due_date >= business_day_service.shop_today())
    stmt = stmt.order_by(CustomOrder.due_date).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [CustomOrderResponse.model_validate(row) for row in rows]


async def _get_or_404(db: AsyncSession, custom_order_id: uuid.UUID) -> CustomOrder:
    row = await db.get(CustomOrder, custom_order_id)
    if row is None:
        raise NotFoundError("Custom order not found")
    return row


@admin_router.put("/{custom_order_id}", response_model=CustomOrderResponse)
async def update_custom_order(
    request: Request,
    custom_order_id: uuid.UUID,
    data: CustomOrderUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.custom.manage")),
):
    custom_order = await _get_or_404(db, custom_order_id)
    changes = data.model_dump(exclude_unset=True)

    # Moving a booking is a booking on the new date, so the new date has to have
    # room. Checked before anything is written, so a refusal leaves the original
    # date intact rather than half-moved.
    new_date = changes.pop("due_date", None)
    if new_date is not None and new_date != custom_order.due_date:
        availability = await custom_order_service.availability_for(db, new_date)
        if not availability.is_available:
            raise BadRequestError(
                f"{new_date.isoformat()} has no capacity left for a custom order."
            )
        custom_order.due_date = new_date

    for field, value in changes.items():
        setattr(custom_order, field, value)
    await db.flush()
    await db.refresh(custom_order)

    await audit_service.log_action(
        db,
        user=admin,
        action="update",
        entity_type="custom_order",
        entity_id=str(custom_order.id),
        changes=data.model_dump(exclude_unset=True, mode="json"),
        request=request,
    )
    return CustomOrderResponse.model_validate(custom_order)


@admin_router.put("/{custom_order_id}/status", response_model=CustomOrderResponse)
async def update_status(
    request: Request,
    custom_order_id: uuid.UUID,
    data: CustomOrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("orders.custom.manage")),
):
    custom_order = await custom_order_service.set_status(
        db, custom_order_id, data.status
    )
    await audit_service.log_action(
        db,
        user=admin,
        action="update",
        entity_type="custom_order",
        entity_id=str(custom_order_id),
        changes={"status": data.status},
        request=request,
    )
    return CustomOrderResponse.model_validate(custom_order)


# ─── Admin: blackout dates ────────────────────────────────────────────────────


class BlackoutCreate(BaseModel):
    blackout_date: date
    reason: str | None = Field(None, max_length=200)


class BlackoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    blackout_date: date
    reason: str | None


@admin_router.get("/blackouts", response_model=list[BlackoutResponse])
async def list_blackouts(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    rows = (
        (
            await db.execute(
                select(CustomOrderBlackout).order_by(CustomOrderBlackout.blackout_date)
            )
        )
        .scalars()
        .all()
    )
    return [BlackoutResponse.model_validate(row) for row in rows]


@admin_router.post(
    "/blackouts", response_model=BlackoutResponse, status_code=status.HTTP_201_CREATED
)
async def create_blackout(
    data: BlackoutCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    """
    Close a date.

    Refuses if something is already booked on it, rather than blacking out a day
    the kitchen has already promised. Move or cancel the booking first — the
    calendar should never disagree with what has been said to a customer.
    """
    existing = await custom_order_service.availability_for(db, data.blackout_date)
    if existing.booked:
        raise BadRequestError(
            f"{data.blackout_date.isoformat()} already has {existing.booked} custom "
            "order(s). Move or cancel them before closing the date."
        )
    blackout = CustomOrderBlackout(blackout_date=data.blackout_date, reason=data.reason)
    db.add(blackout)
    await db.flush()
    await db.refresh(blackout)
    return BlackoutResponse.model_validate(blackout)


@admin_router.delete("/blackouts/{blackout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blackout(
    blackout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("orders.custom.manage")),
):
    blackout = await db.get(CustomOrderBlackout, blackout_id)
    if blackout is None:
        raise NotFoundError("Blackout not found")
    await db.delete(blackout)


__all__ = ["admin_router", "router"]
