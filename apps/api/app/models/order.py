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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    TimestampMixin,
    UUIDMixin,
    business_date_format,
    status_vocabulary,
)
from .pos_order import OrderItemStatusEnum, PosOrderStatusEnum

if TYPE_CHECKING:
    from .branch import Branch
    from .order_delivery import OrderDelivery
    from .order_status_event import OrderStatusEvent
    from .payment_transaction import PaymentTransaction
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
    __table_args__ = (
        # Two terminals can read the same `max(check_number)` and both take it.
        # Partial because the column is NULL for every website order that never
        # reached a register.
        Index(
            "uq_orders_branch_business_date_check_number",
            "branch_id",
            "business_date",
            "check_number",
            unique=True,
            postgresql_where=text(
                "check_number IS NOT NULL "
                "AND branch_id IS NOT NULL "
                "AND business_date IS NOT NULL"
            ),
        ),
        # Migration 099: a typo'd pos_status vanishes from every WHERE clause
        # that feeds a register; the CHECK makes it an error instead.
        status_vocabulary("orders", "pos_status", PosOrderStatusEnum, nullable=True),
        # Migration 100: check numbers, tills and Z-reports join on this string.
        business_date_format("orders"),
    )

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
    #: The small-basket surcharge, charged on delivery orders whose goods come to
    #: no more than `delivery_settings.low_order_threshold`.
    #:
    #: Its own column rather than folded into `delivery_fee`, because the two are
    #: reconciled against different things: the delivery fee is set against what
    #: a courier charges us for that run, and mixing a service charge into it
    #: would make every freight-margin report wrong by fifteen dirhams on exactly
    #: the orders where the margin is tightest.
    low_order_fee: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )
    subtotal: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=0
    )
    total: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    #: How much of `total` has been sent back to the card.
    #:
    #: Not a boolean, because a partial refund is the normal case: the shop
    #: refunds what the customer bought and keeps the fees, since the van was
    #: booked and often already drove. It is also the guard against refunding
    #: twice — the one failure in this area that costs real money — so it is
    #: written in the same transaction as the gateway call that caused it.
    refunded_amount: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )
    #: When the first refund was *issued*, which is not when it landed. Card
    #: refunds sit `pending` at the processor for days.
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
    #: What the customer chose: `card` or `cod` (`PaymentMethodEnum`).
    #:
    #: Not who processed it. Every card order written before gateways were split
    #: out holds `stripe` here, which is why the enum still accepts it — but the
    #: two questions are different ones and this column only answers the first.
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: Who processed it: `stripe`, `ziina`, or `cod` for money taken at the
    #: counter. Chosen by `payment_gateway_router` at session creation, from the
    #: `payment_gateways` table — never by the checkout page.
    payment_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The gateway handle for the *current* attempt: a session while the customer
    #: is paying, the confirmed payment once they have. `payment_transactions`
    #: is the full history; this is the shortcut every existing reader already
    #: uses, kept in step with the latest row.
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
    #: Whoever else's number this order is also known by — an aggregator's or a
    #: marketplace's own reference. Free text and not a foreign key to anything:
    #: it is a string somebody else owns, and its only job is to be printed on
    #: the ticket and read back to them over a phone. Null on everything the
    #: website takes directly, which today is everything.
    external_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

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
    #: The kitchen this order belongs to. Read for its trading hours, which is
    #: what decides whether a terminal may accept the order by itself.
    #:
    #: No `back_populates`: `Branch.orders` would be a collection nothing reads
    #: and everything would have to maintain. Lazy by default and deliberately
    #: so — an async lazy load raises `MissingGreenlet` rather than quietly
    #: issuing a query, which is the behaviour that makes a forgotten
    #: `selectinload` loud instead of slow. The one reader guards on
    #: `inspect(order).unloaded`.
    branch: Mapped[Branch] = relationship("Branch", viewonly=True)
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
    #: The register's tender ledger. Lazy by default like everything else on
    #: this model: the four POS collections here were `lazy="selectin"` for a
    #: while, which meant four extra queries on every order fetched anywhere —
    #: including 2000-row console pages that read none of them — and an escape
    #: hatch of `noload()`s in `order_service` to switch it back off. Callers
    #: that serialise them (`pos_order_service.get_order`, the POS routes' load
    #: options) say `selectinload` explicitly, and a forgotten one is a loud
    #: `MissingGreenlet`, not a quiet N+1 — see `branch` above.
    payments: Mapped[list[OrderPayment]] = relationship(
        "OrderPayment", cascade="all, delete-orphan"
    )
    #: Every attempt to take money for this order online, one row per gateway
    #: session. Distinct from `payments` above, which is the register's tender
    #: ledger — cash in a till and a card on a gateway are not the same object
    #: and were never going to fit in one table.
    payment_transactions: Mapped[list[PaymentTransaction]] = relationship(
        "PaymentTransaction",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="PaymentTransaction.created_at",
    )
    order_charges: Mapped[list[OrderCharge]] = relationship(
        "OrderCharge", cascade="all, delete-orphan"
    )
    order_discounts: Mapped[list[OrderDiscount]] = relationship(
        "OrderDiscount", cascade="all, delete-orphan"
    )
    order_taxes: Mapped[list[OrderTax]] = relationship(
        "OrderTax", cascade="all, delete-orphan"
    )
    #: How this order reaches the customer, and what the courier charged us.
    #: Admin-facing only — never serialised into a storefront response.
    delivery: Mapped[OrderDelivery | None] = relationship(
        "OrderDelivery",
        back_populates="order",
        cascade="all, delete-orphan",
        uselist=False,
    )
    #: Every status this order has reached, oldest first. Written by the
    #: listener in `order_status_event`, not by any caller here — see that
    #: module for why coverage has to be structural.
    #:
    #: Lazy by default: most reads of an `Order` do not want its history, and
    #: the two that do (the timeline and the admin's event list) ask for it.
    status_events: Mapped[list[OrderStatusEvent]] = relationship(
        "OrderStatusEvent",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusEvent.at",
    )

    #: Walks `payments`, so the caller must have loaded it (`selectinload`) —
    #: on an unloaded async instance this raises `MissingGreenlet`, which is
    #: the loud failure the lazy default exists to give. A caller that cannot
    #: guarantee the load sums in SQL instead, like
    #: `noon_send_service.outstanding_balance`.
    @property
    def amount_paid(self) -> Decimal:
        return sum((p.signed_amount for p in self.payments), start=Decimal("0"))

    @property
    def balance_due(self) -> Decimal:
        return Decimal(str(self.total)) - self.amount_paid

    def __repr__(self) -> str:
        return f"<Order {self.order_number}>"


class OrderItem(Base, UUIDMixin, TimestampMixin):
    # `TimestampMixin` arrived with migration 101. Lines are edited in place —
    # voided, partly returned, re-priced by a recalculation — and nothing
    # recorded when. `created_at` is backfilled from `added_at` where the
    # register stamped one and from the parent order's `created_at` where it
    # did not; `added_at` stays, because "when the cashier added it to the
    # check" and "when the row was written" are different claims and the
    # receipts rely on the first.
    __tablename__ = "order_items"
    __table_args__ = (
        # Migration 099. NULL on storefront lines, same as the order's
        # pos_status.
        status_vocabulary("order_items", "status", OrderItemStatusEnum, nullable=True),
    )

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
    #: What the customer asked to have written on this line, captured at order
    #: time like every other snapshot here.
    #:
    #: Deliberately not `kitchen_notes`. That field is typed by a cashier at the
    #: counter; this one is typed by the customer on the website. Sharing a
    #: column would put "no nuts" and "Happy birthday, Sara" in the same string
    #: with nothing to tell them apart — and both of them get printed.
    personalisation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

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
