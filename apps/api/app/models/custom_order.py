"""
Custom cakes: bespoke orders the shop takes by phone, DM, the enquiry form or at
the counter, and makes to a brief.

A custom order **is an order** — an `orders` row with `source = 'custom'` — so it
has the same lines, totals, VAT, customer, address, courier booking, P&L and
ledger as every other channel, and every screen that lists orders can list it.
What it has that no other order has lives here, one row per order, rather than
as custom-only columns on a table every channel shares:

* how the customer is paying, and whether a card fee was put on the bill;
* the enquiry it was converted from, if any;
* when the kitchen docket reached the Sharjah register (the moment the order
  becomes `arrived_at_pos`);
* its **recipe** — the semi-finished bases (ganache, sponges) the kitchen will
  use, chosen per order because no two custom cakes are the same. It belongs to
  the order, not to a line: a three-tier cake is one line and one recipe. It is
  consumed from the ledger when the order is packed, through the same
  `source_event_service` path every order uses (`recipe_service.snapshot_order`
  reads these lines instead of product recipes for this channel).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary


class CustomOrderPaymentTypeEnum(str, enum.Enum):
    """How the customer pays. Never collected by a rider: a custom order is never
    cash on delivery, even when it is paid in cash — the customer pays the shop
    separately, and the courier only carries the cake."""

    BANK_TRANSFER = "bank_transfer"
    CARD = "card"
    CASH = "cash"


class CustomOrderCardFeeModeEnum(str, enum.Enum):
    """Whether a card payment's processing fee is billed as its own line.

    `separate_line` adds an `order_charges` row the customer pays; `included`
    means the line prices already absorb it. Either way the fee is recorded as
    the order's `payment_fee` cost, so the channel's P&L carries it."""

    SEPARATE_LINE = "separate_line"
    INCLUDED = "included"


class CustomOrderCreatedViaEnum(str, enum.Enum):
    ADMIN = "admin"
    POS = "pos"


class CustomOrder(Base, TimestampMixin):
    """The custom-only half of an `orders` row with `source = 'custom'`."""

    __tablename__ = "custom_orders"
    __table_args__ = (
        status_vocabulary(
            "custom_orders", "payment_type", CustomOrderPaymentTypeEnum, nullable=True
        ),
        status_vocabulary(
            "custom_orders", "card_fee_mode", CustomOrderCardFeeModeEnum, nullable=True
        ),
        status_vocabulary("custom_orders", "created_via", CustomOrderCreatedViaEnum),
        # A card payment has to say where its fee went; nothing else has a fee.
        CheckConstraint(
            "(payment_type IS NOT DISTINCT FROM 'card') = (card_fee_mode IS NOT NULL)",
            name="ck_custom_orders_card_fee_mode_iff_card",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        primary_key=True,
    )
    payment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    card_fee_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The storefront enquiry this order was converted from. Unique: one lead
    #: becomes at most one order.
    enquiry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_order_enquiries.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    created_via: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Set once, by the first register to claim the docket (a conditional
    #: UPDATE, so two iPads woken by the same push cannot both print it).
    kitchen_printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    recipe_lines: Mapped[list[CustomOrderRecipeLine]] = relationship(
        back_populates="custom_order",
        cascade="all, delete-orphan",
        order_by="CustomOrderRecipeLine.position",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<CustomOrder {self.order_id}>"


class CustomOrderRecipeLine(Base, UUIDMixin, TimestampMixin):
    """One semi-finished base this order will use, and how much.

    `quantity` is in the item's **ingredient** unit — the unit recipes are
    written in and the unit the consumption poster converts from
    (`source_event_service.post_event` posts `unit="ingredient"`). It may exceed
    what is on hand: the ledger goes negative and the next production or count
    settles it.
    """

    __tablename__ = "custom_order_recipe_lines"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "item_id", name="uq_custom_order_recipe_lines_order_item"
        ),
        CheckConstraint("quantity > 0", name="ck_custom_order_recipe_lines_quantity"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_orders.order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(20, 8), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    custom_order: Mapped[CustomOrder] = relationship(back_populates="recipe_lines")

    def __repr__(self) -> str:
        return f"<CustomOrderRecipeLine {self.item_id} × {self.quantity}>"
