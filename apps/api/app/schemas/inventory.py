"""Schemas for the inventory domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Translations = dict[str, dict[str, str]]

TransactionTypeLiteral = Literal[
    "purchasing",
    "transfer_send",
    "transfer_receive",
    "quantity_adjustment",
    "return_to_supplier",
    "production",
    "consumption_from_production",
    "consumption_from_orders",
    "return_from_orders",
    "return_from_transfers",
    "waste_from_orders",
    "waste_from_production",
    "cost_adjustment",
    "inventory_count",
]

UnitLiteral = Literal["storage", "ingredient"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Categories ───────────────────────────────────────────────────────────────


class InventoryCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    parent_id: UUID | None = None
    display_order: int = 0
    is_active: bool = True


class InventoryCategoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    parent_id: UUID | None = None
    display_order: int | None = None
    is_active: bool | None = None


class InventoryCategoryResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str | None
    parent_id: UUID | None
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─── Warehouses ───────────────────────────────────────────────────────────────


class WarehouseCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    reference: str | None = Field(None, max_length=50)
    is_default: bool = False
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    reference: str | None = Field(None, max_length=50)
    is_default: bool | None = None
    is_active: bool | None = None


class WarehouseResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    name_localized: str | None
    reference: str | None
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ─── Items ────────────────────────────────────────────────────────────────────


class InventoryItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    barcode: str | None = Field(None, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    translations: Translations = Field(default_factory=dict)
    category_id: UUID | None = None
    storage_unit: str = Field("unit", max_length=30)
    ingredient_unit: str = Field("unit", max_length=30)
    storage_to_ingredient_factor: Decimal = Field(Decimal("1"), gt=0)
    minimum_level: Decimal = Field(Decimal("0"), ge=0)
    maximum_level: Decimal = Field(Decimal("0"), ge=0)
    par_level: Decimal = Field(Decimal("0"), ge=0)
    cost: Decimal = Field(Decimal("0"), ge=0)
    costing_method: Literal["fixed", "from_ingredients"] = "fixed"
    yield_percentage: Decimal = Field(Decimal("1"), gt=0, le=1)
    is_product: bool = False
    is_active: bool = True


class InventoryItemUpdate(BaseModel):
    sku: str | None = Field(None, min_length=1, max_length=100)
    barcode: str | None = Field(None, max_length=100)
    name: str | None = Field(None, min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    translations: Translations | None = None
    category_id: UUID | None = None
    storage_unit: str | None = Field(None, max_length=30)
    ingredient_unit: str | None = Field(None, max_length=30)
    storage_to_ingredient_factor: Decimal | None = Field(None, gt=0)
    minimum_level: Decimal | None = Field(None, ge=0)
    maximum_level: Decimal | None = Field(None, ge=0)
    par_level: Decimal | None = Field(None, ge=0)
    cost: Decimal | None = Field(None, ge=0)
    costing_method: Literal["fixed", "from_ingredients"] | None = None
    yield_percentage: Decimal | None = Field(None, gt=0, le=1)
    is_product: bool | None = None
    is_active: bool | None = None


class InventoryItemResponse(ORMModel):
    id: UUID
    sku: str
    barcode: str | None
    name: str
    name_localized: str | None
    translations: Translations
    category_id: UUID | None
    storage_unit: str
    ingredient_unit: str
    storage_to_ingredient_factor: Decimal
    minimum_level: Decimal
    maximum_level: Decimal
    par_level: Decimal
    cost: Decimal
    costing_method: str
    yield_percentage: Decimal
    is_product: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: Populated on the levels report.
    quantity_on_hand: Decimal | None = None
    stock_value: Decimal | None = None


class InventoryLevelResponse(ORMModel):
    id: UUID
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    average_cost: Decimal
    last_counted_at: datetime | None
    item_name: str | None = None
    item_sku: str | None = None
    ingredient_unit: str | None = None
    minimum_level: Decimal | None = None
    par_level: Decimal | None = None
    total_value: Decimal | None = None
    is_below_minimum: bool = False


# ─── Suppliers ────────────────────────────────────────────────────────────────


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=50)
    contact_name: str | None = Field(None, max_length=150)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    address: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    payment_terms_days: int = Field(0, ge=0, le=365)
    notes: str | None = None
    is_active: bool = True


class SupplierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=50)
    contact_name: str | None = Field(None, max_length=150)
    phone: str | None = Field(None, max_length=30)
    email: str | None = Field(None, max_length=255)
    address: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    notes: str | None = None
    is_active: bool | None = None


class SupplierResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    reference: str | None
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    tax_number: str | None
    payment_terms_days: int
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierItemUpsert(BaseModel):
    item_id: UUID
    supplier_sku: str | None = Field(None, max_length=100)
    cost: Decimal = Field(Decimal("0"), ge=0)
    lead_time_days: int = Field(0, ge=0, le=365)
    is_preferred: bool = False


class SupplierItemResponse(ORMModel):
    id: UUID
    supplier_id: UUID
    item_id: UUID
    supplier_sku: str | None
    cost: Decimal
    lead_time_days: int
    is_preferred: bool


# ─── Transactions ─────────────────────────────────────────────────────────────


class TransactionLineInput(BaseModel):
    item_id: UUID
    quantity: Decimal
    unit: UnitLiteral = "storage"
    unit_cost: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None


class InventoryTransactionCreate(BaseModel):
    type: TransactionTypeLiteral
    branch_id: UUID
    warehouse_id: UUID | None = None
    other_branch_id: UUID | None = None
    other_warehouse_id: UUID | None = None
    supplier_id: UUID | None = None
    reason_id: UUID | None = None
    invoice_number: str | None = Field(None, max_length=100)
    invoice_date: date | None = None
    additional_cost: Decimal = Field(Decimal("0"), ge=0)
    paid_tax: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None
    items: list[TransactionLineInput] = Field(min_length=1)


class TransactionLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    unit: str
    conversion_factor: Decimal
    quantity_in_ingredient_unit: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    expected_quantity: Decimal | None
    notes: str | None
    item_name: str | None = None
    item_sku: str | None = None


class InventoryTransactionResponse(ORMModel):
    id: UUID
    reference: str
    type: str
    status: str
    branch_id: UUID
    warehouse_id: UUID | None
    other_branch_id: UUID | None
    supplier_id: UUID | None
    purchase_order_id: UUID | None
    order_id: UUID | None
    reason_id: UUID | None
    business_date: str
    invoice_number: str | None
    invoice_date: date | None
    additional_cost: Decimal
    paid_tax: Decimal
    total_cost: Decimal
    notes: str | None
    creator_id: UUID | None
    poster_id: UUID | None
    posted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[TransactionLineResponse] = []


# ─── Purchase orders ──────────────────────────────────────────────────────────


class PurchaseOrderLineInput(BaseModel):
    item_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: UnitLiteral = "storage"
    unit_cost: Decimal = Field(Decimal("0"), ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: UUID
    branch_id: UUID
    warehouse_id: UUID | None = None
    delivery_date: date | None = None
    additional_cost: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None
    items: list[PurchaseOrderLineInput] = Field(min_length=1)


class PurchaseOrderUpdate(BaseModel):
    supplier_id: UUID | None = None
    warehouse_id: UUID | None = None
    delivery_date: date | None = None
    additional_cost: Decimal | None = Field(None, ge=0)
    notes: str | None = None
    items: list[PurchaseOrderLineInput] | None = None


class PurchaseOrderLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    received_quantity: Decimal
    outstanding_quantity: Decimal
    unit: str
    conversion_factor: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    item_name: str | None = None
    item_sku: str | None = None


class PurchaseOrderResponse(ORMModel):
    id: UUID
    reference: str
    status: str
    supplier_id: UUID
    branch_id: UUID
    warehouse_id: UUID | None
    business_date: str
    delivery_date: date | None
    additional_cost: Decimal
    total_cost: Decimal
    notes: str | None
    creator_id: UUID | None
    submitter_id: UUID | None
    approver_id: UUID | None
    submitted_at: datetime | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[PurchaseOrderLineResponse] = []
    supplier_name: str | None = None


class ReceiveLine(BaseModel):
    purchase_order_item_id: UUID
    quantity: Decimal = Field(gt=0)


class ReceivePurchaseOrderRequest(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1)


# ─── Recipes ──────────────────────────────────────────────────────────────────


class RecipeLine(BaseModel):
    item_id: UUID
    quantity: Decimal = Field(gt=0)
    inactive_in_order_types: list[str] = Field(default_factory=list)


class RecipeUpsert(BaseModel):
    ingredients: list[RecipeLine] = Field(default_factory=list)


class RecipeLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    inactive_in_order_types: list[str] = []
    item_name: str | None = None
    item_sku: str | None = None
    ingredient_unit: str | None = None
    unit_cost: Decimal | None = None
    line_cost: Decimal | None = None


class RecipeResponse(BaseModel):
    product_id: UUID
    ingredients: list[RecipeLineResponse] = []
    total_cost: Decimal = Decimal("0")


# ─── Adjustments & counts ─────────────────────────────────────────────────────


class OpenCountRequest(BaseModel):
    branch_id: UUID
    warehouse_id: UUID | None = None
    #: Limit the count to these items; omit to count everything on hand.
    item_ids: list[UUID] | None = None
    notes: str | None = None


class CountLine(BaseModel):
    item_id: UUID
    counted_quantity: Decimal = Field(ge=0)


class CloseCountRequest(BaseModel):
    items: list[CountLine] = Field(min_length=1)


class WasteRequest(BaseModel):
    branch_id: UUID
    item_id: UUID
    #: Always written off; the sign is not the caller's to choose.
    quantity: Decimal = Field(gt=0)
    #: Waste during prep rather than from a sold order.
    from_production: bool = False
    reason_id: UUID | None = None
    notes: str | None = None


class CostAdjustmentRequest(BaseModel):
    branch_id: UUID
    item_id: UUID
    warehouse_id: UUID | None = None
    new_average_cost: Decimal = Field(ge=0)
    notes: str | None = None


class CostAdjustmentResponse(BaseModel):
    item_id: str
    warehouse_id: str | None
    quantity_on_hand: Decimal
    previous_average_cost: Decimal
    new_average_cost: Decimal
    value_change: Decimal
    adjusted_by: str | None
    notes: str | None


class QuantityAdjustmentRequest(BaseModel):
    branch_id: UUID
    item_id: UUID
    #: Signed: negative writes stock off.
    quantity_delta: Decimal
    reason_id: UUID | None = None
    notes: str | None = None
