from __future__ import annotations

import uuid
import enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .user import User


class OrderStatusEnum(str, enum.Enum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    PACKED = "packed"
    CANCELLED = "cancelled"


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
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped[User | None] = relationship("User", back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )

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
    product_name_localized: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    product_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    options_price: Mapped[Any] = mapped_column(
        Numeric(10, 2), nullable=False, server_default="0"
    )
    unit_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    total_price: Mapped[Any] = mapped_column(Numeric(10, 2), nullable=False)
    selected_options_snapshot: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    # Relationships
    order: Mapped[Order] = relationship("Order", back_populates="items")

    def __repr__(self) -> str:
        return f"<OrderItem {self.product_name} x{self.quantity}>"
