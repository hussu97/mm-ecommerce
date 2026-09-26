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
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
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
    utcnow,
)

if TYPE_CHECKING:
    from .branch import Branch


class InventoryTransactionTypeEnum(str, enum.Enum):
    """The ways stock moves, mirroring Foodics' transaction types.

    Every quantity or value change in the ledger is one of these. Their direction
    is in ``TRANSACTION_SIGN`` below and their human labels live in the admin
    ledger's movement-label map.
    """

    PURCHASING = "purchasing"
    TRANSFER_SEND = "transfer_send"
    TRANSFER_RECEIVE = "transfer_receive"
    QUANTITY_ADJUSTMENT = "quantity_adjustment"
    RETURN_TO_SUPPLIER = "return_to_supplier"
    PRODUCTION = "production"
    CONSUMPTION_FROM_PRODUCTION = "consumption_from_production"
    CONSUMPTION_FROM_ORDERS = "consumption_from_orders"
    RETURN_FROM_ORDERS = "return_from_orders"
    WASTE_FROM_ORDERS = "waste_from_orders"
    WASTE_FROM_PRODUCTION = "waste_from_production"
    COST_ADJUSTMENT = "cost_adjustment"
    INVENTORY_COUNT = "inventory_count"
    OPENING_BALANCE = "opening_balance"
    INTERNAL_USE = "internal_use"
    # Raw material consumed in production beyond what a recipe captures — off-recipe
    # usage, or producing a good that has no item/recipe in the system. Entered by
    # the shop on the raw-materials/packaging report as an extra deduction, distinct
    # from the recipe-driven CONSUMPTION_FROM_PRODUCTION so it stays reportable apart.
    EXTRA_PRODUCTION_USE = "extra_production_use"
    # Re-costs one production batch (linked by ``correction_group_id``) at a
    # stated unit cost, for a batch whose recorded inputs were incomplete. Moves
    # no stock; the costing engine re-prices the batch and everything drawn from
    # it (rule 7 in ``costing_engine``).
    PRODUCTION_RESTATEMENT = "production_restatement"


#: Direction each transaction type moves stock in the branch it is posted to.
#: Cost adjustments and production restatements change value without changing
#: quantity, hence 0.
TRANSACTION_SIGN: dict[str, int] = {
    InventoryTransactionTypeEnum.PURCHASING.value: 1,
    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value: 1,
    InventoryTransactionTypeEnum.PRODUCTION.value: 1,
    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value: 1,
    InventoryTransactionTypeEnum.TRANSFER_SEND.value: -1,
    InventoryTransactionTypeEnum.RETURN_TO_SUPPLIER.value: -1,
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value: -1,
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: -1,
    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value: -1,
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value: -1,
    # Adjustments and counts carry a signed quantity of their own.
    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value: 1,
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value: 1,
    InventoryTransactionTypeEnum.OPENING_BALANCE.value: 1,
    InventoryTransactionTypeEnum.INTERNAL_USE.value: -1,
    InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value: -1,
    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value: 0,
    InventoryTransactionTypeEnum.PRODUCTION_RESTATEMENT.value: 0,
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
    #: Cancelled after the fact. Any received stock is reversed and the weighted
    #: -average cost restated; the row is kept for audit and excluded from
    #: valuation/VAT/spend.
    VOIDED = "voided"


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
    __table_args__ = (
        CheckConstraint(
            "kind IN ('raw_material', 'packaging', 'semi_finished', "
            "'produced_good', 'resale_good')",
            name="ck_inventory_item_kind",
        ),
        CheckConstraint(
            "tracking_mode IN ('stocked', 'phantom')",
            name="ck_inventory_item_tracking_mode",
        ),
        CheckConstraint(
            "storage_to_ingredient_factor > 0",
            name="ck_inventory_item_positive_conversion",
        ),
        CheckConstraint("cost >= 0", name="ck_inventory_item_nonnegative_cost"),
        CheckConstraint(
            "yield_percentage > 0 AND yield_percentage <= 1",
            name="ck_inventory_item_valid_yield",
        ),
    )

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
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="raw_material", index=True
    )
    tracking_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="stocked", index=True
    )
    storage_zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    count_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
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
    #: Days a unit stays sellable once made. Caps the replenishment forecast's
    #: production so it never plans more than the item can sell before it expires.
    shelf_life_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="14"
    )

    # There is no per-item cost column: cost is FIFO, held in the item's cost
    # layers and summarised on ``InventoryLevel.average_cost`` per warehouse. The
    # redundant, never-updated ``cost``/``costing_method`` columns were dropped
    # (migration 267) — an item's cost is derived via
    # ``cost_layer_service.item_average_cost`` (0 until its first receipt).
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

    # Catalogue exports need the stable category reference, and using the
    # relationship keeps that lookup in the query instead of issuing one query
    # per inventory row.
    category: Mapped[InventoryCategory | None] = relationship("InventoryCategory")
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
    #: Held in the item's storage unit — the canonical stock unit. Purchases and
    #: counts are in storage units; a recipe consumption is converted from its
    #: ingredient unit to storage before it moves this figure.
    quantity: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Weighted-average cost per storage unit — the item's cost at this warehouse,
    #: derived from its surviving FIFO layers (the source of truth for valuation).
    average_cost: Mapped[Any] = mapped_column(
        Numeric(20, 10), nullable=False, server_default="0"
    )
    last_counted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    projected_through_sequence: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(
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
    """A vendor stock is purchased from.

    ``is_vat_deductible`` (default true) says whether the VAT charged on this
    supplier's invoices is recoverable — when it is, a purchase order splits the
    tax out into ``purchase_order_items.vat_amount`` for the reclaim report. The
    tax stays inside the item's FIFO cost either way; the split is a record for
    reclaim, not a deduction from inventory value.
    """

    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    is_vat_deductible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: "Flexible item mapping": when true, a purchase order for this supplier may
    #: add ANY active purchasable inventory item, not only the ones mapped to the
    #: supplier — the mapped items remain the suggested shortlist. When false, only
    #: mapped items may be ordered (the strict default).
    allow_any_item: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: When true, a purchase order for this supplier may carry free-text
    #: **miscellaneous** lines (name/qty/unit/total) that are NOT inventory items
    #: — one-off buys tracked only for expense history and VAT recovery. Such a
    #: supplier (e.g. Amazon AE) shows up in the PO picker even with no mapped
    #: items. See ``PurchaseOrderMiscItem``.
    allows_misc_items: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
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

    contacts: Mapped[list[SupplierContact]] = relationship(
        "SupplierContact",
        back_populates="supplier",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Supplier {self.name}>"


class SupplierContact(Base, UUIDMixin, TimestampMixin):
    """A person to reach at a supplier.

    A supplier may have several. A contact must carry a way to reach them — the
    ``ck_supplier_contact_reachable`` CHECK refuses a row that is a name and
    nothing else (no email and no phone).
    """

    __tablename__ = "supplier_contacts"
    __table_args__ = (
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL",
            name="ck_supplier_contact_reachable",
        ),
    )

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    supplier: Mapped[Supplier] = relationship("Supplier", back_populates="contacts")

    def __repr__(self) -> str:
        return f"<SupplierContact {self.name}>"


class SupplierItem(Base, UUIDMixin, TimestampMixin):
    """A supplier↔item mapping: which items a supplier can supply.

    Only a **purchased** item may be mapped — one whose ``kind`` is a raw
    material, packaging or resale good and that owns no recipe (a recipe means
    the item is produced, not bought). That rule is enforced in the service on
    write; one item may be mapped to several suppliers.

    The mapping carries no price: an item's cost comes only from the purchase
    orders it is actually received on, never a stored "usual" figure.
    """

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
    __table_args__ = (
        # Migration 099: `is_posted` — and with it whether stock has actually
        # moved — hangs off this string.
        status_vocabulary("inventory_transactions", "status", TransactionStatusEnum),
        status_vocabulary(
            "inventory_transactions", "type", InventoryTransactionTypeEnum
        ),
        # Migration 100.
        business_date_format("inventory_transactions"),
        UniqueConstraint(
            "idempotency_key", name="uq_inventory_transaction_idempotency"
        ),
        CheckConstraint(
            "status <> 'closed' OR (posting_sequence IS NOT NULL AND posted_at IS NOT NULL)",
            name="ck_inventory_closed_posting_metadata",
        ),
        # Migration 285: the P&L reads each order's cost of goods by `order_id`,
        # once per row of an orders page of up to two thousand.
        Index(
            "ix_inventory_transactions_order_id",
            "order_id",
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )

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
    #: Ledger truth follows this sequence, never a provider-supplied order time.
    posting_sequence: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True, index=True
    )
    source_accepted_sequence: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reverses_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    correction_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    items: Mapped[list[InventoryTransactionItem]] = relationship(
        "InventoryTransactionItem",
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
        # Deterministic order by primary key, so the live posting path iterates
        # lines in the same order the ledger rebuild replays them (which orders by
        # this id). Two lines for the same item in one transaction then lay/consume
        # layers identically live and on rebuild.
        order_by="InventoryTransactionItem.id",
    )

    @property
    def is_posted(self) -> bool:
        return self.status == TransactionStatusEnum.CLOSED.value

    def __repr__(self) -> str:
        return f"<InventoryTransaction {self.reference} {self.type}>"


class InventoryTransactionItem(Base, UUIDMixin):
    """One line of a stock movement, recorded in the unit it was entered in."""

    __tablename__ = "inventory_transaction_items"
    __table_args__ = (
        CheckConstraint(
            "unit IN ('storage', 'ingredient')",
            name="ck_inventory_transaction_item_unit",
        ),
    )

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
    #: `quantity` normalised into the item's storage unit — the canonical unit
    #: the level and `signed_quantity` are kept in. Every movement records this.
    quantity_in_storage_unit: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: `quantity` normalised into the item's ingredient unit — the recipe/prep
    #: view of the same movement, carried alongside the storage figure so a
    #: consumption shows both (e.g. 2 tsp and the 8 g it took off the shelf).
    quantity_in_ingredient_unit: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Immutable signed movement (in storage units) used by projection rebuilds.
    signed_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    unit_cost: Mapped[Any] = mapped_column(
        Numeric(20, 10), nullable=False, server_default="0"
    )
    #: Moving-average cost immediately before this immutable movement.
    previous_unit_cost: Mapped[Any | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Counts record what was expected so the variance is preserved.
    expected_quantity: Mapped[Any | None] = mapped_column(Numeric(16, 4), nullable=True)
    balance_after_quantity: Mapped[Any | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )
    balance_after_value: Mapped[Any | None] = mapped_column(
        Numeric(20, 4), nullable=True
    )
    recipe_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipe_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    #: The original line this line reverses, so FIFO cost layers can be restored
    #: to (or removed from) exactly the layers the original movement touched.
    reverses_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recipe_path: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="[]")
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_lots.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    __table_args__ = (
        # Migration 099.
        status_vocabulary("purchase_orders", "status", PurchaseOrderStatusEnum),
        # Migration 100.
        business_date_format("purchase_orders"),
        CheckConstraint("origin IN ('admin', 'pos')", name="ck_purchase_orders_origin"),
    )

    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default=PurchaseOrderStatusEnum.DRAFT.value,
        index=True,
    )
    #: Where the order was raised: 'admin' (received later on the till) or 'pos'
    #: (created and auto-received at the till in one go).
    origin: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="admin"
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
    #: The supplier's own PO/invoice number, for reconciliation (optional).
    supplier_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: GCS object key of the uploaded invoice image in the private finance bucket
    #: (signed on read), plus its content type. Not a public URL.
    invoice_object_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    invoice_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    additional_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Frozen money totals. ``total_gross`` is what was paid (VAT-inclusive) and
    #: is the value that reaches inventory; ``vat_total`` is the recoverable slice
    #: recorded for the reclaim report; ``subtotal_net`` = gross − vat.
    subtotal_net: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    vat_total: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    total_gross: Mapped[Any] = mapped_column(
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
    voided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    items: Mapped[list[PurchaseOrderItem]] = relationship(
        "PurchaseOrderItem",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    #: Free-text, non-inventory lines (only for suppliers with
    #: ``allows_misc_items``). They never post to stock; they feed PO totals and
    #: the VAT reclaim report only.
    misc_items: Mapped[list[PurchaseOrderMiscItem]] = relationship(
        "PurchaseOrderMiscItem",
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
    #: What the user keyed for this line: the total, VAT-inclusive, in money.
    entered_total: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: The recoverable VAT slice of ``entered_total`` (0 when the supplier is not
    #: VAT-deductible). Recorded for the reclaim report; still inside the cost.
    vat_amount: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: ``entered_total − vat_amount`` — the net-of-VAT line total.
    net_total: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: Gross cost per storage unit (``entered_total ÷ quantity``) — the value that
    #: becomes the FIFO layer's unit cost on receipt.
    unit_cost: Mapped[Any] = mapped_column(
        Numeric(20, 10), nullable=False, server_default="0"
    )
    #: Gross line total (== ``entered_total``); kept for report continuity.
    total_cost: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    #: The receiver's note when what arrived differs from what was ordered — the
    #: same short/excess variance capture a transfer line carries. Set on receipt,
    #: required when ``received_quantity`` ≠ ``quantity``; null otherwise.
    variance_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    purchase_order: Mapped[PurchaseOrder] = relationship(
        "PurchaseOrder", back_populates="items"
    )

    @property
    def variance_quantity(self) -> Any:
        """received − ordered: negative when short, positive when over, 0 exact."""
        from decimal import Decimal

        return Decimal(str(self.received_quantity or 0)) - Decimal(str(self.quantity))

    @property
    def outstanding_quantity(self) -> Any:
        from decimal import Decimal

        return max(
            Decimal(str(self.quantity)) - Decimal(str(self.received_quantity or 0)),
            Decimal("0"),
        )

    def __repr__(self) -> str:
        return f"<PurchaseOrderItem item={self.item_id} qty={self.quantity}>"


class PurchaseOrderMiscItem(Base, UUIDMixin):
    """A non-inventory line on a purchase order.

    Bought from a supplier tagged ``allows_misc_items`` for a specific need, on
    the same invoice as regular stock, but **never** tracked as inventory: no
    ``item_id``, no FIFO layer, no stock movement. It exists only so the spend
    shows in purchase history and its VAT is recoverable. Money mirrors
    ``PurchaseOrderItem``: ``entered_total`` is gross (VAT-inclusive),
    ``vat_amount`` the recoverable slice, ``net_total`` = gross − vat.
    """

    __tablename__ = "purchase_order_misc_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchase_order_misc_items_quantity"),
        CheckConstraint(
            "period_to >= period_from", name="ck_purchase_order_misc_items_period"
        ),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[Any] = mapped_column(Numeric(16, 4), nullable=False)
    storage_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    entered_total: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    vat_amount: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    net_total: Mapped[Any] = mapped_column(
        Numeric(16, 4), nullable=False, server_default="0"
    )
    unit_cost: Mapped[Any] = mapped_column(
        Numeric(20, 10), nullable=False, server_default="0"
    )
    #: What the spend is — shared across suppliers. An ``admin_only`` category
    #: hides the line from the till and from admins without the restricted
    #: permission (``po_misc_service``).
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_misc_categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: The days the spend covers (inclusive). The P&L spreads ``net_total``
    #: equally over them rather than booking it on the PO date.
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    purchase_order: Mapped[PurchaseOrder] = relationship(
        "PurchaseOrder", back_populates="misc_items"
    )
    category: Mapped[PurchaseOrderMiscCategory] = relationship(
        "PurchaseOrderMiscCategory", lazy="selectin"
    )

    @property
    def category_name(self) -> str | None:
        return self.category.name if self.category is not None else None

    @property
    def category_admin_only(self) -> bool:
        return bool(self.category is not None and self.category.admin_only)

    def __repr__(self) -> str:
        return f"<PurchaseOrderMiscItem {self.name} qty={self.quantity}>"


class PurchaseOrderMiscCategory(Base, UUIDMixin, TimestampMixin):
    """What a misc PO line is for (Groceries, Rent, …) — not supplier-specific.

    ``admin_only`` marks a confidential category: its lines never reach the till
    (as if they were not on the PO) and admin shows them only to holders of
    ``inventory.purchase_orders.restricted_misc``.
    """

    __tablename__ = "purchase_order_misc_categories"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    admin_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrderMiscCategory {self.name}>"


class PurchaseOrderMiscPeriodUnitEnum(str, enum.Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class PurchaseOrderMiscPeriod(Base, UUIDMixin, TimestampMixin):
    """A preset the misc-line period picker offers ("This month", "This year").

    ``unit`` × ``length`` says how the picker asks for the range (a month picker
    for ``month``) and what it pre-fills: the current ``length``-long block. It
    is only a convenience — a line stores its dates, never the preset.
    """

    __tablename__ = "purchase_order_misc_periods"
    __table_args__ = (
        CheckConstraint(
            "unit IN ('day', 'week', 'month')",
            name="ck_purchase_order_misc_periods_unit",
        ),
        CheckConstraint("length >= 1", name="ck_purchase_order_misc_periods_length"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    length: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrderMiscPeriod {self.name}>"


# ─── FIFO cost layers ───────────────────────────────────────────────────────────


class CostLayerSourceKindEnum(str, enum.Enum):
    """How a cost layer came to exist. All are inbound (stock-adding) events."""

    PURCHASING = "purchasing"
    OPENING_BALANCE = "opening_balance"
    #: Auto-created for pre-existing on-hand that had no cost history, the first
    #: time a purchase gives us a price to value it at.
    BACKFILL = "backfill"
    TRANSFER_RECEIVE = "transfer_receive"
    PRODUCTION = "production"
    RETURN_FROM_ORDERS = "return_from_orders"
    POSITIVE_ADJUSTMENT = "positive_adjustment"
    COUNT_OVERAGE = "count_overage"


class InventoryCostLayer(Base, UUIDMixin, TimestampMixin):
    """
    One FIFO cost layer: a quantity of stock received at a known unit cost.

    Costing is **FIFO**. Every inbound movement lays down a layer; every issue
    consumes the oldest layers first (ordered by ``posting_sequence`` then
    ``layer_index``), recording an `InventoryCostLayerConsumption` per layer it
    draws from. ``InventoryLevel.average_cost`` is derived from the surviving
    layers (Σ remaining × cost ÷ Σ remaining), so a sale changes what remains is
    worth without ever blending an average.

    A layer is a **projection of the immutable ledger** — the truth is
    `inventory_transaction_items` (``signed_quantity``, ``unit_cost``,
    ``posting_sequence``). Layers and consumptions can always be regenerated by
    `ledger_service.reconcile_levels(apply=True)`, so they carry no immutability
    trigger of their own.
    """

    __tablename__ = "inventory_cost_layers"
    __table_args__ = (
        status_vocabulary(
            "inventory_cost_layers", "source_kind", CostLayerSourceKindEnum
        ),
        CheckConstraint(
            "remaining_quantity >= 0", name="ck_inventory_cost_layer_remaining"
        ),
        CheckConstraint(
            "original_quantity >= 0", name="ck_inventory_cost_layer_original"
        ),
        CheckConstraint("unit_cost >= 0", name="ck_inventory_cost_layer_cost"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Denormalised so the branch advisory lock and per-branch rebuilds can scope
    #: layers without joining through the warehouse.
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    #: The FIFO ordering key — copied from the inbound transaction's posting
    #: sequence, so layers consume in the exact order stock was posted.
    posting_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Tie-break within a single transaction (the line's ordinal), so two layers
    #: born of the same posting still have a deterministic FIFO order.
    layer_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    original_quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    remaining_quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    #: Per storage unit, net of recoverable VAT.
    unit_cost: Mapped[Any] = mapped_column(Numeric(20, 10), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The cost is an estimate: this stock (or the stock it was priced from) is
    #: still waiting on its next priced receipt, which will re-cost it.
    cost_is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: The ledger line whose price this layer carries — its own receipt, or the
    #: later purchase order that priced stock found by a count.
    priced_by_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<InventoryCostLayer item={self.item_id} "
            f"remaining={self.remaining_quantity}@{self.unit_cost}>"
        )


class InventoryCostLayerConsumption(Base, UUIDMixin):
    """
    One draw from one cost layer by one outbound ledger line — the COGS trail.

    A consumption of 350 units that spans two layers writes two rows, so the
    cost of any issue is fully attributable to the receipts it drew from. Written
    append-only by the posting path; a reversal restores the layer's
    ``remaining_quantity`` rather than editing these.
    """

    __tablename__ = "inventory_cost_layer_consumptions"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_inventory_cost_consumption_quantity"),
        # The replay's per-warehouse diff and the fast path's "anything still
        # waiting on a price here?" probe both filter on (item, warehouse).
        Index(
            "ix_inventory_cost_layer_consumptions_item_warehouse",
            "item_id",
            "warehouse_id",
        ),
    )

    consuming_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Null only for a shortfall row — stock issued that no layer covered
    #: (negative-stock branches), costed at a fallback and flagged below.
    layer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_cost_layers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    unit_cost: Mapped[Any] = mapped_column(Numeric(20, 10), nullable=False)
    total_cost: Mapped[Any] = mapped_column(Numeric(20, 4), nullable=False)
    posting_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: True when the layers ran dry (negative stock is allowed for this branch)
    #: and this row was costed at a fallback rather than a real layer.
    is_shortfall: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    cost_is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    priced_by_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<InventoryCostLayerConsumption layer={self.layer_id} "
            f"qty={self.quantity}@{self.unit_cost}>"
        )


class InventoryLineCost(Base):
    """
    What one closed ledger line is worth *now*, per the v3 costing engine.

    ``inventory_transaction_items.total_cost`` is what the line was booked at
    the moment it posted and is immutable; this is its current, projected cost —
    re-costed when stock it touched later learns its price (a count overage
    priced by a later PO, a sale that ran ahead of its delivery). Both are kept
    so a screen can say "booked at X". Rebuilt by the engine; never edited.
    """

    __tablename__ = "inventory_line_costs"
    __table_args__ = (
        Index(
            "ix_inventory_line_costs_item_history",
            "item_id",
            "warehouse_id",
            "posting_sequence",
        ),
        Index("ix_inventory_line_costs_warehouse_id", "warehouse_id"),
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
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
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        nullable=False,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="RESTRICT"),
        nullable=False,
    )
    posting_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Signed storage quantity, as on the ledger line.
    quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    unit_cost: Mapped[Any] = mapped_column(Numeric(20, 10), nullable=False)
    total_cost: Mapped[Any] = mapped_column(Numeric(20, 4), nullable=False)
    booked_unit_cost: Mapped[Any] = mapped_column(Numeric(20, 10), nullable=False)
    is_provisional: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    priced_by_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inventory_transaction_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    running_quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    running_value: Mapped[Any] = mapped_column(Numeric(20, 4), nullable=False)
    #: A pre-v3 cost adjustment the engine deliberately ignores.
    superseded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class InventoryCostingState(Base):
    """
    Singleton bookkeeping for the costing engine.

    ``cutover_sequence`` is the last posting before v3 went live: cost
    adjustments at or below it were workarounds for the old engine's bugs and
    are skipped by the replay. The rest records the last estate replay.
    """

    __tablename__ = "inventory_costing_state"
    __table_args__ = (
        CheckConstraint("id", name="ck_inventory_costing_state_singleton"),
    )

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default="true")
    cutover_sequence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_estate_replay_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_estate_replay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_estate_changes: Mapped[int | None] = mapped_column(Integer, nullable=True)


class InventoryCostingDirty(Base):
    """
    A warehouse that was restated on its own and wants an estate replay.

    A warehouse replay runs inside the posting that needed it, under that
    branch's lock only, so it can re-cost this warehouse but not the branches
    its transfers fed. This row asks the scheduler to finish the job estate-wide.
    One row per warehouse (not a shared flag) so two branches posting at once
    never queue on the same row lock.
    """

    __tablename__ = "inventory_costing_dirty"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    dirty_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


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
