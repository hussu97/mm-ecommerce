"""
Custom cakes: the ones that have to be booked rather than picked off a shelf.

A stock brownie box is made in bulk and sold until it runs out. A custom cake is
a day of somebody's work — it has to be booked for a date, and only so many can
be booked for the same date. That capacity is the whole point of this module:
without it the shop takes four wedding cakes for the same Saturday and finds out
on Friday.

Two things book that capacity, and they are equally real:

* an order placed on the website for a product tagged customisable, and
* an order taken over Instagram or WhatsApp, which has no order behind it at all
  and never will.

The second is why `CustomOrder` exists as its own row rather than a few columns
on `orders`. Most of this bakery's custom work arrives as a DM, and a calendar
that only knew about website orders would show a free Saturday that is in fact
fully booked — which is worse than no calendar, because someone would trust it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary


class CustomOrderSourceEnum(str, enum.Enum):
    """Where the request came from. `website` is the only one with an order."""

    WEBSITE = "website"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    WALK_IN = "walk_in"
    OTHER = "other"


class CustomOrderStatusEnum(str, enum.Enum):
    #: Someone asked. Holds a slot, because a maybe still has to be baked if it
    #: turns into a yes, and releasing it on confirmation is easier than
    #: apologising for double-booking.
    ENQUIRY = "enquiry"
    CONFIRMED = "confirmed"
    IN_PRODUCTION = "in_production"
    READY = "ready"
    COMPLETED = "completed"
    #: Releases the slot.
    CANCELLED = "cancelled"


#: Statuses that still occupy a slot on their date.
OCCUPIES_SLOT = frozenset(
    {
        CustomOrderStatusEnum.ENQUIRY.value,
        CustomOrderStatusEnum.CONFIRMED.value,
        CustomOrderStatusEnum.IN_PRODUCTION.value,
        CustomOrderStatusEnum.READY.value,
    }
)


class CustomOrder(Base, UUIDMixin, TimestampMixin):
    """One booked custom cake, whatever channel asked for it."""

    __tablename__ = "custom_orders"
    __table_args__ = (
        # The calendar's only query: "what is booked on this date". Everything
        # else — admin lists, a customer's history — is rarer than this.
        Index("ix_custom_orders_due_date_status", "due_date", "status"),
        # Migration 099: a typo'd status here is a slot the capacity check
        # either never releases or never counts.
        status_vocabulary("custom_orders", "status", CustomOrderStatusEnum),
        # Migration 138. Ours, not a provider's: a request arrives by one of
        # four routes we chose, and the sibling `status` has been constrained
        # since 099.
        status_vocabulary("custom_orders", "source", CustomOrderSourceEnum),
    )

    #: The date the cake is wanted. Not nullable: a custom order with no date is
    #: not a booking, and the capacity check has nothing to count.
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=CustomOrderStatusEnum.ENQUIRY.value,
        index=True,
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=CustomOrderSourceEnum.WEBSITE.value,
    )

    #: The storefront order, when there is one. Null for everything that arrived
    #: as a DM, which is most of them. SET NULL rather than CASCADE: deleting an
    #: order must not silently free a slot the kitchen is still working on.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    #: Denormalised deliberately. An Instagram order has no user and no order,
    #: and the person taking it on their phone has a name and a number and
    #: nothing else.
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: What was asked for, in the customer's words.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: Written on the cake. Kept apart from `description` because it is the one
    #: field that must be reproduced exactly, spelling included.
    cake_message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    flavour: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    servings: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Reference photographs, in R2. The single most useful thing a customer
    #: sends and the thing a chat thread loses first.
    reference_image_urls: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )

    quoted_total: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    deposit_amount: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    deposit_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Which kitchen is making it, when that has been decided.
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: For a website order, the product that was tagged customisable.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Whatever else the customer answered. Free-form so the brief can grow
    #: without a migration every time someone asks a new question.
    brief: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def occupies_slot(self) -> bool:
        return self.status in OCCUPIES_SLOT

    def __repr__(self) -> str:
        return f"<CustomOrder {self.due_date} {self.customer_name} {self.status}>"


class CustomOrderBlackout(Base, UUIDMixin, TimestampMixin):
    """
    A date that takes no custom orders at all, whatever the capacity says.

    Eid, a holiday, the week the oven is being serviced. Separate from setting
    capacity to zero for the day because it carries a reason, and because
    capacity is a standing number rather than a diary.
    """

    __tablename__ = "custom_order_blackouts"

    blackout_date: Mapped[date] = mapped_column(
        Date, nullable=False, unique=True, index=True
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def __repr__(self) -> str:
        return f"<CustomOrderBlackout {self.blackout_date}>"
