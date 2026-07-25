"""
Inventory: items, stock levels, suppliers, and the transaction ledger that moves
stock between them.

Two ideas carry the whole domain:

1. **Every quantity change is a transaction.** `InventoryLevel.quantity` is a
   cached projection of `InventoryTransactionItem` rows, never the source of
   truth, so stock can always be rebuilt and audited.
2. **Storage unit vs ingredient unit.** Flour is bought in 25kg sacks and used in
   grams. Items store both units plus a conversion factor, and every transaction
   records the unit it was entered in, so a recipe change never silently
   rescales historical cost.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .branch import Branch


class CostingMethodEnum(str, enum.Enum):
    FIXED = "fixed"
    FROM_INGREDIENTS = "from_ingredients"


class InventoryTransactionTypeEnum(str, enum.Enum):
    """The twelve ways stock moves, mirroring Foodics' transaction types."""

    PURCHASING = "purchasing"
    TRANSFER_SEND = "transfer_send"
    TRANSFER_RECEIVE = "transfer_receive"
    QUANTITY_ADJUSTMENT = "quantity_adjustment"
    RETURN_TO_SUPPLIER = "return_to_supplier"
    PRODUCTION = "production"
    CONSUMPTION_FROM_PRODUCTION = "consumption_from_production"
    CONSUMPTION_FROM_ORDERS = "consumption_from_orders"
    RETURN_FROM_ORDERS = "return_from_orders"
    RETURN_FROM_TRANSFERS = "return_from_transfers"
    WASTE_FROM_ORDERS = "waste_from_orders"
    WASTE_FROM_PRODUCTION = "waste_from_production"
    COST_ADJUSTMENT = "cost_adjustment"
    INVENTORY_COUNT = "inventory_count"


#: Direction each transaction type moves stock in the branch it is posted to.
#: Cost adjustments change value without changing quantity, hence 0.
TRANSACTION_SIGN: dict[str, int] = {
    InventoryTransactionTypeEnum.PURCHASING.value: 1,
    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value: 1,
    InventoryTransactionTypeEnum.PRODUCTION.value: 1,
    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value: 1,
    InventoryTransactionTypeEnum.RETURN_FROM_TRANSFERS.value: 1,
    InventoryTransactionTypeEnum.TRANSFER_SEND.value: -1,
    InventoryTransactionTypeEnum.RETURN_TO_SUPPLIER.value: -1,
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value: -1,
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: -1,
    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value: -1,
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value: -1,
    # Adjustments and counts carry a signed quantity of their own.
    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value: 1,
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value: 1,
    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value: 0,
}


class TransactionStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    DECLINED = "declined"
    CLOSED = "closed"  # posted; stock has moved


class PurchaseOrderStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    PARTIALLY_RECEIVED = "partially_received"
    CLOSED = "closed"


class InventoryCategory(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "inventory_categories"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<InventoryCategory {self.name}>"


class Warehouse(Base, UUIDMixin, TimestampMixin):
    """
    A stock location. Every branch has at least one; a production kitchen may
    have several (dry store, chiller, freezer).
    """

    __tablename__ = "warehouses"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    branch: Mapped[Branch] = relationship("Branch")

    def __repr__(self) -> str:
        return f"<Warehouse {self.name}>"


class InventoryItem(Base, UUIDMixin, TimestampMixin):
    """A raw material, semi-finished good, or tracked retail item."""

    __tablename__ = "inventory_items"

    sku: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Units. Purchased in `storage_unit`, consumed in `ingredient_unit`.
    storage_unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="unit"
    )
    ingredient_unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="unit"
    )
    storage_to_ingredient_factor: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="1"
    )

    # Reorder thresholds, per item; the levels report compares stock against these.
    minimum_level: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    maximum_level: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    par_level: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )

    cost: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="0"
    )
    costing_method: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=CostingMethodEnum.FIXED.value
    )
    # Produced items lose weight in the process; 0.9 means 10% is lost.
    yield_percentage: Mapped[Any] = mapped_column(
        Numeric(6, 4), nullable=False, server_default="1"
    )
    #: True when this item is itself sold (a retail SKU rather than a raw material).
    is_product: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    levels: Mapped[list[InventoryLevel]] = relationship(
        "InventoryLevel", back_populates="item", cascade="all, delete-orphan"
    )
    ingredients: Mapped[list[InventoryItemIngredient]] = relationship(
        "InventoryItemIngredient",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys="InventoryItemIngredient.parent_item_id",
    )

    def to_ingredient_units(self, storage_quantity: Any) -> Any:
        """Convert a purchase quantity into consumption units."""
        from decimal import Decimal

        return Decimal(str(storage_quantity)) * Decimal(
            str(self.storage_to_ingredient_factor)
        )

    def __repr__(self) -> str:
        return f"<InventoryItem {self.sku} {self.name}>"


class InventoryLevel(Base, UUIDMixin, TimestampMixin):
    """
    Current stock of one item in one warehouse.

    A cached projection of the transaction ledger, kept for fast reads. Rebuild
    it from `inventory_transaction_items` if it is ever suspected of drift.
    """

    __tablename__ = "inventory_levels"
    __table_args__ = (
        UniqueConstraint("item_id", "warehouse_id", name="uq_inventory_level"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Held in the item's ingredient unit.
    quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Weighted-average cost per ingredient unit.
    average_cost: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="0"
    )
    last_counted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    item: Mapped[InventoryItem] = relationship("InventoryItem", back_populates="levels")

    @property
    def total_value(self) -> Any:
        from decimal import Decimal

        return Decimal(str(self.quantity)) * Decimal(str(self.average_cost))

    def __repr__(self) -> str:
        return f"<InventoryLevel item={self.item_id} qty={self.quantity}>"


class Supplier(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_terms_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Supplier {self.name}>"


class SupplierItem(Base, UUIDMixin, TimestampMixin):
    """What a given supplier charges for a given item, and how fast they deliver."""

    __tablename__ = "supplier_items"
    __table_args__ = (
        UniqueConstraint("supplier_id", "item_id", name="uq_supplier_item"),
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supplier_sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="0"
    )
    lead_time_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_preferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    def __repr__(self) -> str:
        return f"<SupplierItem supplier={self.supplier_id} item={self.item_id}>"


class InventoryTransaction(Base, UUIDMixin, TimestampMixin):
    """
    One movement of stock. Nothing changes `InventoryLevel` except posting a
    transaction, which is what makes the ledger authoritative.
    """

    __tablename__ = "inventory_transactions"

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TransactionStatusEnum.DRAFT.value,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Destination branch/warehouse for transfers.
    other_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    other_warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Set for stock consumed by a sale, so cost of goods ties back to the order.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reasons.id", ondelete="SET NULL"), nullable=True
    )

    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    additional_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    paid_tax: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    poster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list[InventoryTransactionItem]] = relationship(
        "InventoryTransactionItem",
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_posted(self) -> bool:
        return self.status == TransactionStatusEnum.CLOSED.value

    def __repr__(self) -> str:
        return f"<InventoryTransaction {self.reference} {self.type}>"


class InventoryTransactionItem(Base, UUIDMixin):
    """One line of a stock movement, recorded in the unit it was entered in."""

    __tablename__ = "inventory_transaction_items"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: As keyed by the user, in `unit`.
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="storage"
    )
    #: Snapshot of the item's factor at entry time, so later unit changes cannot
    #: rewrite the history of this movement.
    conversion_factor: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="1"
    )
    #: `quantity` normalised into the item's ingredient unit.
    quantity_in_ingredient_unit: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    unit_cost: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="0"
    )
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Counts record what was expected so the variance is preserved.
    expected_quantity: Mapped[Any | None] = mapped_column(Numeric(16, 4), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    transaction: Mapped[InventoryTransaction] = relationship(
        "InventoryTransaction", back_populates="items"
    )

    def __repr__(self) -> str:
        return f"<InventoryTransactionItem item={self.item_id} qty={self.quantity}>"


class PurchaseOrder(Base, UUIDMixin, TimestampMixin):
    """
    A request to a supplier. Distinct from a stock movement: approving a PO does
    not change stock — receiving against it creates a `purchasing` transaction.
    """

    __tablename__ = "purchase_orders"

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=PurchaseOrderStatusEnum.DRAFT.value,
        index=True,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    additional_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list[PurchaseOrderItem]] = relationship(
        "PurchaseOrderItem",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def is_fully_received(self) -> bool:
        return bool(self.items) and all(
            (i.received_quantity or 0) >= i.quantity for i in self.items
        )

    def __repr__(self) -> str:
        return f"<PurchaseOrder {self.reference} {self.status}>"


class PurchaseOrderItem(Base, UUIDMixin):
    __tablename__ = "purchase_order_items"

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    received_quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    unit: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="storage"
    )
    conversion_factor: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="1"
    )
    unit_cost: Mapped[Any] = mapped_column(
        Numeric(16, 6), nullable=False, server_default="0"
    )
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )

    purchase_order: Mapped[PurchaseOrder] = relationship(
        "PurchaseOrder", back_populates="items"
    )

    @property
    def outstanding_quantity(self) -> Any:
        from decimal import Decimal

        return max(
            Decimal(str(self.quantity)) - Decimal(str(self.received_quantity or 0)),
            Decimal("0"),
        )

    def __repr__(self) -> str:
        return f"<PurchaseOrderItem item={self.item_id} qty={self.quantity}>"


# ─── Recipes ──────────────────────────────────────────────────────────────────


class ProductIngredient(Base, UUIDMixin, TimestampMixin):
    """
    Recipe line: how much of an inventory item one unit of a product consumes.
    Selling the product depletes these.
    """

    __tablename__ = "product_ingredients"
    __table_args__ = (
        UniqueConstraint("product_id", "item_id", name="uq_product_ingredient"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: In the item's ingredient unit.
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    #: Order types this ingredient is *not* used for (e.g. no dine-in packaging).
    inactive_in_order_types: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    def __repr__(self) -> str:
        return f"<ProductIngredient product={self.product_id} item={self.item_id}>"


class ModifierOptionIngredient(Base, UUIDMixin, TimestampMixin):
    """Recipe line for a modifier option, e.g. an extra shot uses 9g of coffee."""

    __tablename__ = "modifier_option_ingredients"
    __table_args__ = (
        UniqueConstraint(
            "modifier_option_id", "item_id", name="uq_modifier_option_ingredient"
        ),
    )

    modifier_option_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)

    def __repr__(self) -> str:
        return f"<ModifierOptionIngredient option={self.modifier_option_id}>"


class InventoryItemIngredient(Base, UUIDMixin, TimestampMixin):
    """
    Bill of materials for a produced item — brownie batter is made from flour,
    butter, chocolate. Producing the parent consumes these children.
    """

    __tablename__ = "inventory_item_ingredients"
    __table_args__ = (
        UniqueConstraint(
            "parent_item_id", "item_id", name="uq_inventory_item_ingredient"
        ),
    )

    parent_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)

    parent: Mapped[InventoryItem] = relationship(
        "InventoryItem",
        back_populates="ingredients",
        foreign_keys=[parent_item_id],
    )

    def __repr__(self) -> str:
        return f"<InventoryItemIngredient parent={self.parent_item_id}>"
