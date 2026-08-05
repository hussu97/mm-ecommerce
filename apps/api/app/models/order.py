from __future__ import annotations

import uuid
import enum
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .order_delivery import OrderDelivery
    from .pos_order import (
        OrderCharge,
        OrderDiscount,
        OrderPayment,
        OrderTax,
    )
    from .user import User


class OrderStatusEnum(str, enum.Enum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    PACKED = "packed"
    #: The parcel has left the kitchen. On an integrated zone this is set by the
    #: courier's own PICKED_UP webhook rather than by hand, so it means the
    #: driver is holding the box, not that someone remembered to click.
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    #: A rider reached the door and could not hand the parcel over. A status of
    #: its own rather than a note on the delivery row, because until it was one
    #: the order still read `out_for_delivery` — the screen said the cake was
    #: coming while the courier record said nobody had taken it, and only the
    #: second of those was true. Not terminal: the order is paid for and still
    #: ours to deliver, so it can be re-dispatched out of here.
    UNDELIVERED = "undelivered"
    CANCELLED = "cancelled"
    PAYMENT_FAILED = "payment_failed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


class DeliveryMethodEnum(str, enum.Enum):
    DELIVERY = "delivery"
    PICKUP = "pickup"


class Order(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "orders"

    order_number: Mapped[str] = mapped_column(
        String(30), unique=True, nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: The language the customer was reading when they placed it — `en` or `ar`.
    #:
    #: A property of the order, not of the customer: a guest has no account to
    #: hang a preference on, and the useful question is not "what does this
    #: person prefer" but "what were they reading when they placed this". Every
    #: email about the order is written in it.
    locale: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="en", default="en"
    )
    delivery_method: Mapped[DeliveryMethodEnum] = mapped_column(
        Enum(
            DeliveryMethodEnum,
            name="deliverymethodenum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    delivery_fee: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    subtotal: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    total: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[OrderStatusEnum] = mapped_column(
        Enum(
            OrderStatusEnum,
            name="orderstatusenum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=OrderStatusEnum.CREATED,
        index=True,
    )
    promo_code_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shipping_address_snapshot: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vat_rate: Mapped[Any] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.0500")
    )
    vat_amount: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    total_excl_vat: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: When the checkout told this customer their order would arrive.
    #:
    #: A record of what was said, not a calculation to repeat. Everything else
    #: about fulfilment gets sharper as the order moves and is derived fresh each
    #: time it is read; this one is fixed the moment somebody is shown it, which
    #: is why it is a column rather than a function.
    #:
    #: Null for an order placed before this existed, and for one placed with no
    #: pin to read a zone off. `fulfilment_service` falls back to deriving an
    #: estimate for those, which is what it used to do for all of them.
    promised_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: `time` or `day` — see `delivery_service.DeliveryEstimate.precision`.
    promised_precision: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ─── POS fields ───────────────────────────────────────────────────────────
    # Null for storefront orders. `status` above keeps the e-commerce lifecycle;
    # `pos_status` models the counter lifecycle, which is a different shape.
    is_pos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    #: The kitchen that made it. Required, and stamped with the row rather than
    #: onto it afterwards: an order nobody is making prints nowhere and reaches
    #: no register, so it is not a state worth being able to represent.
    #: `order_service.resolve_branch` decides it — the zone's own kitchen, then
    #: the configured pickup branch, then any active branch at all.
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tables.id", ondelete="SET NULL"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    till_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tills.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    order_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    #: Which channel rang this up — `online` or `cashier`. Not nullable: null
    #: used to mean "a storefront order from before this column existed", which
    #: made every reader carry a footnote. Backfilled by `061`.
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    pos_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    delivery_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )
    # Per-branch, per-day counter shown to the customer ("Order 42").
    check_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guests: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    kitchen_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Walk-in customers are not registered users, so the POS captures them inline.
    customer_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    charges_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    rounding_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tips_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )

    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reasons.id", ondelete="SET NULL"), nullable=True
    )
    # Set on the child when a check is split or joined, so both halves stay traceable.
    original_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    user: Mapped[User | None] = relationship(
        "User", back_populates="orders", foreign_keys=[user_id]
    )
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    payments: Mapped[list[OrderPayment]] = relationship(
        "OrderPayment", cascade="all, delete-orphan", lazy="selectin"
    )
    order_charges: Mapped[list[OrderCharge]] = relationship(
        "OrderCharge", cascade="all, delete-orphan", lazy="selectin"
    )
    order_discounts: Mapped[list[OrderDiscount]] = relationship(
        "OrderDiscount", cascade="all, delete-orphan", lazy="selectin"
    )
    order_taxes: Mapped[list[OrderTax]] = relationship(
        "OrderTax", cascade="all, delete-orphan", lazy="selectin"
    )
    #: How this order reaches the customer, and what the courier charged us.
    #: Admin-facing only — never serialised into a storefront response.
    delivery: Mapped[OrderDelivery | None] = relationship(
        "OrderDelivery",
        back_populates="order",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @property
    def amount_paid(self) -> Decimal:
        return sum((p.signed_amount for p in self.payments), start=Decimal("0"))

    @property
    def balance_due(self) -> Decimal:
        return Decimal(str(self.total)) - self.amount_paid

    def __repr__(self) -> str:
        return f"<Order {self.order_number}>"


class OrderItem(Base, UUIDMixin):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Snapshots at order time
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    product_translations: Mapped[Any | None] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Weight sold, for products priced per kilo. Quantity stays whole —
    #: a 0.4 kg line is still one line on the check.
    weight: Mapped[Any | None] = mapped_column(Numeric(10, 3), nullable=True)
    base_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    options_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    unit_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    total_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    selected_options_snapshot: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    # ─── POS fields ───────────────────────────────────────────────────────────
    status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    returned_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    discount_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tax_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tax_exclusive_unit_price: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    tax_exclusive_total_price: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    # Set when the cashier keys the price (products with pricing_method="open").
    is_open_price: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    kitchen_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which course this line is fired with; null on an uncoursed check.
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="SET NULL"), nullable=True
    )
    kitchen_flow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kitchen_flows.id", ondelete="SET NULL"),
        nullable=True,
    )
    sent_to_kitchen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    added_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reasons.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    order: Mapped[Order] = relationship("Order", back_populates="items")

    @property
    def effective_quantity(self) -> int:
        """Quantity still counting toward the bill after any partial return."""
        return max(self.quantity - (self.returned_quantity or 0), 0)

    def __repr__(self) -> str:
        return f"<OrderItem {self.product_name} x{self.quantity}>"
