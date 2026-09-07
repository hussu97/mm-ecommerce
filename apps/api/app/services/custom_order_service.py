"""
Booking a custom cake, and refusing to book too many.

The capacity check lives here and nowhere else. Both callers that can fill a
date — the storefront placing an order and an admin typing in an Instagram
request — go through `book`, because a check that exists on one path and not the
other is not a check at all: the calendar would show a free Saturday that a DM
had already taken.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.phone import normalise_phone
from app.models.business_settings import BusinessSettings
from app.models.custom_order import (
    OCCUPIES_SLOT,
    CustomOrder,
    CustomOrderBlackout,
    CustomOrderSourceEnum,
    CustomOrderStatusEnum,
)
from app.models.product import Product
from app.services.pos import business_day_service

logger = logging.getLogger(__name__)

__all__ = [
    "DayAvailability",
    "availability_for",
    "availability_range",
    "book",
    "earliest_bookable_date",
    "move",
    "set_status",
]


def _capacity_lock_key(day: date) -> int:
    """
    A stable advisory-lock key for one date's capacity.

    A date has no single row to lock — capacity is a count over the day's
    bookings — so the guard serialises on the *date itself*. Derived by hash so
    two requests for the same day always take the same lock and different days
    never block each other, in a namespace of its own (the "cocap:" prefix) so it
    cannot collide with the fixed singleton keys the background sweeps hold. A
    full signed 64-bit value, which is what `pg_advisory_xact_lock` takes.
    """
    digest = hashlib.blake2b(
        f"cocap:{day.isoformat()}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class DayAvailability:
    day: date
    capacity: int
    booked: int
    is_blackout: bool
    blackout_reason: str | None = None

    @property
    def remaining(self) -> int:
        if self.is_blackout:
            return 0
        return max(self.capacity - self.booked, 0)

    @property
    def is_available(self) -> bool:
        return self.remaining > 0


async def _settings(db: AsyncSession) -> BusinessSettings:
    existing = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    if existing is None:
        existing = BusinessSettings()
        db.add(existing)
        await db.flush()
    return existing


async def earliest_bookable_date(
    db: AsyncSession, product: Product | None = None
) -> date:
    """
    The first date a customer may choose.

    Today plus the notice this product needs. A product's own `lead_time_days`
    wins over the business default, so a tiered wedding cake can demand a week
    while a simple message-on-top cake needs two days.
    """
    settings = await _settings(db)
    lead = settings.custom_order_lead_days
    if product is not None and product.lead_time_days is not None:
        lead = product.lead_time_days
    return business_day_service.shop_today() + timedelta(days=max(lead, 0))


async def _blackouts(
    db: AsyncSession, start: date, end: date
) -> dict[date, str | None]:
    rows = (
        (
            await db.execute(
                select(CustomOrderBlackout).where(
                    CustomOrderBlackout.blackout_date >= start,
                    CustomOrderBlackout.blackout_date <= end,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.blackout_date: row.reason for row in rows}


async def _booked_counts(db: AsyncSession, start: date, end: date) -> dict[date, int]:
    """
    How many slots each date has taken.

    Counts every status in `OCCUPIES_SLOT`, enquiries included — a maybe still
    has to be baked if it becomes a yes, and freeing the slot when it is
    confirmed is easier than apologising for a double booking.
    """
    rows = await db.execute(
        select(CustomOrder.due_date, func.count(CustomOrder.id))
        .where(
            CustomOrder.due_date >= start,
            CustomOrder.due_date <= end,
            CustomOrder.status.in_(sorted(OCCUPIES_SLOT)),
        )
        .group_by(CustomOrder.due_date)
    )
    return {row[0]: int(row[1]) for row in rows}


async def availability_range(
    db: AsyncSession, start: date, end: date
) -> list[DayAvailability]:
    """Every date in the window with its remaining capacity."""
    if end < start:
        raise BadRequestError("The end of the range is before its start")
    settings = await _settings(db)
    blackouts = await _blackouts(db, start, end)
    booked = await _booked_counts(db, start, end)

    days: list[DayAvailability] = []
    cursor = start
    while cursor <= end:
        days.append(
            DayAvailability(
                day=cursor,
                capacity=settings.custom_orders_per_day,
                booked=booked.get(cursor, 0),
                is_blackout=cursor in blackouts,
                blackout_reason=blackouts.get(cursor),
            )
        )
        cursor += timedelta(days=1)
    return days


async def availability_for(db: AsyncSession, day: date) -> DayAvailability:
    return (await availability_range(db, day, day))[0]


async def _assert_bookable(
    db: AsyncSession,
    day: date,
    *,
    product: Product | None,
    allow_past: bool,
) -> None:
    """
    Whether this date can take another custom order.

    `allow_past` exists for the admin: an order taken on Instagram is often
    typed in after the fact, and refusing to record something that already
    happened would just push it back into the chat thread this calendar is
    meant to replace. The storefront never sets it.
    """
    settings = await _settings(db)

    if not allow_past:
        earliest = await earliest_bookable_date(db, product)
        if day < earliest:
            lead = (earliest - business_day_service.shop_today()).days
            raise BadRequestError(
                f"This needs {lead} day(s) notice — the earliest available date "
                f"is {earliest.isoformat()}."
            )
        latest = business_day_service.shop_today() + timedelta(
            days=settings.custom_order_max_days_ahead
        )
        if day > latest:
            raise BadRequestError(
                f"Bookings are only open to {latest.isoformat()}. "
                "Please contact us directly for a date beyond that."
            )

    availability = await availability_for(db, day)
    if availability.is_blackout:
        reason = availability.blackout_reason or "we are not taking custom orders"
        raise ConflictError(f"{day.isoformat()} is unavailable — {reason}.")
    if not availability.is_available:
        raise ConflictError(
            f"{day.isoformat()} is fully booked for custom orders. "
            "Please choose another date."
        )


async def book(
    db: AsyncSession,
    *,
    due_date: date,
    customer_name: str,
    description: str,
    source: str = CustomOrderSourceEnum.WEBSITE.value,
    status: str = CustomOrderStatusEnum.ENQUIRY.value,
    customer_phone: str | None = None,
    customer_email: str | None = None,
    cake_message: str | None = None,
    flavour: str | None = None,
    size_label: str | None = None,
    servings: int | None = None,
    reference_image_urls: list[str] | None = None,
    quoted_total=None,
    deposit_amount=None,
    order_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    brief: dict | None = None,
    admin_notes: str | None = None,
    created_by_id: uuid.UUID | None = None,
    allow_past: bool = False,
) -> CustomOrder:
    """
    Take a slot on a date, or refuse.

    The single writer. An admin adding an Instagram order and a customer
    checking out both land here, so a date cannot be filled by one in a way the
    other cannot see.

    The capacity check and the insert run under a per-date advisory lock held to
    the end of the request. Without it the check is count-then-insert with a gap:
    two bookings for the last slot each read "one free" before either has
    written, and the date is promised twice — the last-slot race a single row
    lock cannot express, because a date's capacity is a count and not a row. See
    `advisory_lock.held_for_request`.
    """
    product = await db.get(Product, product_id) if product_id else None

    async with advisory_lock.held_for_request(db, _capacity_lock_key(due_date)):
        await _assert_bookable(db, due_date, product=product, allow_past=allow_past)

        custom_order = CustomOrder(
            due_date=due_date,
            status=status,
            source=source,
            order_id=order_id,
            customer_name=customer_name.strip(),
            # Normalised to E.164 like every other customer number, so the
            # operator typing "0501234567" and the website's "+971501234567" are
            # one format. The raw is kept where it will not parse — a booking
            # still needs ringing.
            customer_phone=normalise_phone(customer_phone) or customer_phone,
            customer_email=customer_email,
            description=description.strip(),
            cake_message=cake_message,
            flavour=flavour,
            size_label=size_label,
            servings=servings,
            reference_image_urls=reference_image_urls or [],
            quoted_total=quoted_total,
            deposit_amount=deposit_amount or 0,
            product_id=product_id,
            branch_id=branch_id,
            brief=brief or {},
            admin_notes=admin_notes,
            created_by_id=created_by_id,
        )
        db.add(custom_order)
        await db.flush()
        await db.refresh(custom_order)

    logger.info(
        "Custom order booked for %s (%s, source=%s)",
        due_date.isoformat(),
        customer_name,
        source,
    )
    return custom_order


async def set_status(
    db: AsyncSession, custom_order_id: uuid.UUID, status: str
) -> CustomOrder:
    """
    Move a booking along, checking capacity if it is coming back to life.

    Reviving a cancelled order is the one transition that can overfill a date:
    the slot was released when it was cancelled and may have been taken since.
    """
    valid = {member.value for member in CustomOrderStatusEnum}
    if status not in valid:
        raise BadRequestError(f"Unknown status '{status}'")

    custom_order = await db.get(CustomOrder, custom_order_id)
    if custom_order is None:
        raise NotFoundError("Custom order not found")

    reviving = not custom_order.occupies_slot and status in OCCUPIES_SLOT
    if reviving:
        availability = await availability_for(db, custom_order.due_date)
        if not availability.is_available:
            raise ConflictError(
                f"{custom_order.due_date.isoformat()} has been filled since this "
                "was cancelled. Change the date before reopening it."
            )

    custom_order.status = status
    await db.flush()
    await db.refresh(custom_order)
    return custom_order


async def move(
    db: AsyncSession, custom_order: CustomOrder, new_date: date
) -> CustomOrder:
    """
    Move a booking to another date, if that date has room.

    Moving a booking *is* a booking on the new date, so the new date has to have
    a free slot — the same rule `book` enforces, and it used to be spelled a
    second time inside the admin update route where it could quietly drift from
    this one. It lives here now, beside `book`, under the same per-date advisory
    lock so a move and a fresh booking cannot both take the last slot on a day.

    A no-op move (new date equals the current one) short-circuits: a date is not
    "full for this order" against itself, and taking the lock to prove that would
    only be ceremony.
    """
    if new_date == custom_order.due_date:
        return custom_order

    async with advisory_lock.held_for_request(db, _capacity_lock_key(new_date)):
        availability = await availability_for(db, new_date)
        if not availability.is_available:
            raise BadRequestError(
                f"{new_date.isoformat()} has no capacity left for a custom order."
            )
        custom_order.due_date = new_date
        await db.flush()
    return custom_order
