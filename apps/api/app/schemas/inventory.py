"""Schemas for the inventory domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Translations = dict[str, dict[str, str]]

TransactionTypeLiteral = Literal[
    "opening_balance",
    "internal_use",
    "purchasing",
    "transfer_send",
    "transfer_receive",
    "quantity_adjustment",
    "return_to_supplier",
    "production",
    "consumption_from_production",
    "consumption_from_orders",
    "return_from_orders",
    "waste_from_orders",
    "waste_from_production",
    "cost_adjustment",
    "inventory_count",
]

#: The subset an operator may post by hand through `POST /inventory/transactions`.
#: Deliberately excludes every system-coupled type — `transfer_send`/
#: `transfer_receive` (a bare send evaporates stock with no paired receive; use
#: the transfer-orders flow), the order/production consumption, return and waste
#: types, and `transfer` returns — all of which are emitted by their own services
#: in sequence and would corrupt the ledger if injected unpaired here.
ManualTransactionTypeLiteral = Literal[
    "opening_balance",
    "quantity_adjustment",
    "cost_adjustment",
    "inventory_count",
    "internal_use",
    "purchasing",
    "return_to_supplier",
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
    yield_percentage: Decimal = Field(Decimal("1"), gt=0, le=1)
    is_product: bool = False
    kind: Literal[
        "raw_material", "packaging", "semi_finished", "produced_good", "resale_good"
    ] = "raw_material"
    tracking_mode: Literal["stocked", "phantom"] = "stocked"
    storage_zone: str | None = Field(None, max_length=100)
    count_order: int = 0
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
    yield_percentage: Decimal | None = Field(None, gt=0, le=1)
    is_product: bool | None = None
    kind: (
        Literal[
            "raw_material", "packaging", "semi_finished", "produced_good", "resale_good"
        ]
        | None
    ) = None
    tracking_mode: Literal["stocked", "phantom"] | None = None
    storage_zone: str | None = Field(None, max_length=100)
    count_order: int | None = None
    is_active: bool | None = None


class ItemSupplierRef(BaseModel):
    """A compact view of one supplier that can supply an item, for the items list."""

    supplier_id: UUID
    supplier_name: str


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
    #: The item's current cost per storage unit, derived from its FIFO layers
    #: (0 until first receipt/production). Populated by the endpoint, not a column.
    average_cost: Decimal = Decimal("0")
    #: The same FIFO cost expressed per *ingredient* unit (``average_cost`` ÷ the
    #: storage→ingredient factor, via ``canonical_cost_for_unit``). The recipe
    #: console prices its lines in ingredient units, so it renders this rather
    #: than repeating the conversion client-side (money math stays server-side).
    ingredient_unit_cost: Decimal = Decimal("0")
    yield_percentage: Decimal
    is_product: bool
    kind: str
    tracking_mode: str
    storage_zone: str | None
    count_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: Populated on the levels report.
    quantity_on_hand: Decimal | None = None
    stock_value: Decimal | None = None
    #: The suppliers that can supply this item, filled by the list endpoint so
    #: the items table can show them without a query per row.
    suppliers: list[ItemSupplierRef] = []


class InventoryLevelResponse(ORMModel):
    id: UUID
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    average_cost: Decimal
    last_counted_at: datetime | None
    projected_through_sequence: int | None = None
    reconciled_at: datetime | None = None
    item_name: str | None = None
    item_sku: str | None = None
    #: The stock unit `quantity`, `average_cost` and `total_value` are in — the
    #: canonical unit the ledger keeps. `ingredient_unit` is the recipe unit,
    #: carried for reference.
    storage_unit: str | None = None
    ingredient_unit: str | None = None
    minimum_level: Decimal | None = None
    par_level: Decimal | None = None
    total_value: Decimal | None = None
    is_below_minimum: bool = False
    branch_id: UUID | None = None
    branch_name: str | None = None
    warehouse_name: str | None = None


# ─── Suppliers ────────────────────────────────────────────────────────────────


class SupplierContactInput(BaseModel):
    """One contact. Must carry an email or a phone — a name alone is refused."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=150)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=30)
    is_primary: bool = False

    @model_validator(mode="after")
    def _reachable(self) -> SupplierContactInput:
        if not (self.email or self.phone):
            raise ValueError("A contact needs an email or a phone, not just a name")
        return self


class SupplierContactResponse(ORMModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    is_primary: bool


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=50)
    is_vat_deductible: bool = True
    #: Flexible item mapping — allow a PO for this supplier to add any active
    #: purchasable item, not just the mapped ones.
    allow_any_item: bool = False
    #: Allow free-text miscellaneous (non-inventory) lines on this supplier's POs.
    allows_misc_items: bool = False
    address: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    payment_terms_days: int = Field(0, ge=0, le=365)
    notes: str | None = None
    is_active: bool = True
    contacts: list[SupplierContactInput] = Field(default_factory=list)


class SupplierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    reference: str | None = Field(None, max_length=50)
    is_vat_deductible: bool | None = None
    allow_any_item: bool | None = None
    allows_misc_items: bool | None = None
    address: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    payment_terms_days: int | None = Field(None, ge=0, le=365)
    notes: str | None = None
    is_active: bool | None = None
    #: When present, replaces the whole contact set; omit to leave contacts as-is.
    contacts: list[SupplierContactInput] | None = None


class SupplierMappedItem(BaseModel):
    """A compact view of one item a supplier can supply, for the list page."""

    item_id: UUID
    item_name: str | None = None
    item_sku: str | None = None


class SupplierResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    reference: str | None
    is_vat_deductible: bool
    allow_any_item: bool = False
    allows_misc_items: bool = False
    address: str | None
    tax_number: str | None
    payment_terms_days: int
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    contacts: list[SupplierContactResponse] = []
    #: The items this supplier can supply, filled by the list endpoint so the
    #: supplier table can show them without a query per row.
    mapped_items: list[SupplierMappedItem] = []


class SupplierItemUpsert(BaseModel):
    item_id: UUID
    supplier_sku: str | None = Field(None, max_length=100)
    lead_time_days: int = Field(0, ge=0, le=365)
    is_preferred: bool = False


class SupplierItemResponse(ORMModel):
    id: UUID
    supplier_id: UUID
    item_id: UUID
    supplier_sku: str | None
    lead_time_days: int
    is_preferred: bool
    #: Filled by the endpoint for the picker on both apps.
    item_name: str | None = None
    item_sku: str | None = None
    storage_unit: str | None = None


# ─── Transactions ─────────────────────────────────────────────────────────────


class TransactionLineInput(BaseModel):
    item_id: UUID
    quantity: Decimal
    unit: UnitLiteral = "storage"
    unit_cost: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None


class InventoryTransactionCreate(BaseModel):
    # `forbid`, so a client that still sends `other_branch_id`/`other_warehouse_id`
    # (the cross-branch transfer fields this endpoint no longer accepts) is
    # refused with a 422 rather than having them silently dropped.
    model_config = ConfigDict(extra="forbid")

    #: Only hand-posted manual types. Cross-branch transfers are refused here —
    #: they have their own sequenced flow — so there is no `other_branch_id`.
    type: ManualTransactionTypeLiteral
    branch_id: UUID
    warehouse_id: UUID | None = None
    supplier_id: UUID | None = None
    reason_id: UUID | None = None
    invoice_number: str | None = Field(None, max_length=100)
    invoice_date: date | None = None
    additional_cost: Decimal = Field(Decimal("0"), ge=0)
    paid_tax: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None
    #: Required: a retried POST (double-click, network retry) must not post the
    #: movement twice. The endpoint returns the first transaction for a key.
    idempotency_key: str = Field(min_length=8, max_length=200)
    items: list[TransactionLineInput] = Field(min_length=1)


class TransactionLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    unit: str
    conversion_factor: Decimal
    #: The movement in the canonical storage unit (what actually left the shelf).
    quantity_in_storage_unit: Decimal
    #: The same movement in the ingredient unit (the recipe view), so a
    #: consumption shows both — e.g. 2 tsp and the 8 g it took off the shelf.
    quantity_in_ingredient_unit: Decimal
    unit_cost: Decimal
    previous_unit_cost: Decimal | None = None
    total_cost: Decimal
    signed_quantity: Decimal | None = None
    balance_after_quantity: Decimal | None = None
    balance_after_value: Decimal | None = None
    recipe_version_id: UUID | None = None
    #: The recipe expansion this movement came from: a list of paths, each path a
    #: list of hop dicts ({owner_kind, owner_id, recipe_version_id, item_id}, all
    #: strings). Order consumption, production and returns now all emit this one
    #: shape — production used to write a flat list of dicts, which is why this was
    #: once a `list[Any]`; migration 200 rewrote those rows.
    recipe_path: list[list[dict[str, str]]] = []
    lot_id: UUID | None = None
    expected_quantity: Decimal | None
    notes: str | None
    item_name: str | None = None
    item_sku: str | None = None
    #: The item's inventory category, backfilled from the loaded item so a count
    #: sheet can group its lines. Both null for an uncategorised item (sort last).
    category_name: str | None = None
    category_order: int | None = None


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
    #: The display name of whoever posted the movement (the poster, or the creator
    #: when no distinct poster). Resolved by the read endpoints so a manual stock
    #: count reads as a submission with an author, the way a shift report does;
    #: null when the user cannot be resolved.
    posted_by_name: str | None = None
    posted_at: datetime | None
    posting_sequence: int | None = None
    source_accepted_sequence: int | None = None
    occurred_at: datetime | None = None
    idempotency_key: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    reverses_transaction_id: UUID | None = None
    correction_group_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    items: list[TransactionLineResponse] = []
    #: The human reference of the document that caused this movement — the order
    #: number, the transfer/return reference, the shift report's name+date, the PO
    #: reference, or "Reversal of …". Filled by the list endpoint so the ledger
    #: reads without a second lookup; null where the source is manual or unresolved.
    source_reference: str | None = None
    #: The admin path to that document's detail page, so the ledger can link to it
    #: (order → order detail, report/transfer → their detail). Null when there is
    #: no page to link to.
    source_link: str | None = None


# ─── Purchase orders ──────────────────────────────────────────────────────────


class PurchaseOrderLineInput(BaseModel):
    """One PO line. The user keys the quantity and the line's total cost.

    ``entered_total`` is VAT-inclusive money for the whole line; the server
    derives the per-unit cost and, for a VAT-deductible supplier, the recoverable
    VAT slice.
    """

    item_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: UnitLiteral = "storage"
    entered_total: Decimal = Field(Decimal("0"), ge=0)


class PurchaseOrderMiscLineInput(BaseModel):
    """One free-text, non-inventory PO line (supplier must allow misc items).

    The user names it and keys quantity, its storage unit, and the VAT-inclusive
    line total. It never becomes an inventory item; the server derives the unit
    cost and recoverable VAT the same way it does for a regular line.
    """

    name: str = Field(min_length=1, max_length=200)
    quantity: Decimal = Field(gt=0)
    storage_unit: str = Field(min_length=1, max_length=30)
    entered_total: Decimal = Field(Decimal("0"), ge=0)


class PurchaseOrderCreate(BaseModel):
    supplier_id: UUID
    branch_id: UUID
    warehouse_id: UUID | None = None
    delivery_date: date | None = None
    supplier_reference: str | None = Field(None, max_length=100)
    additional_cost: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None
    items: list[PurchaseOrderLineInput] = Field(default_factory=list)
    misc_items: list[PurchaseOrderMiscLineInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_a_line(self):
        if not self.items and not self.misc_items:
            raise ValueError("A purchase order needs at least one line")
        return self


class PurchaseOrderUpdate(BaseModel):
    supplier_id: UUID | None = None
    warehouse_id: UUID | None = None
    delivery_date: date | None = None
    supplier_reference: str | None = Field(None, max_length=100)
    additional_cost: Decimal | None = Field(None, ge=0)
    notes: str | None = None
    items: list[PurchaseOrderLineInput] | None = None
    #: When present, replaces the whole misc-line set; omit to leave as-is.
    misc_items: list[PurchaseOrderMiscLineInput] | None = None


class PosPurchaseOrderCreate(BaseModel):
    """Create-and-receive in one call from the till.

    The branch comes from the device, so it is not in the body. The invoice image
    is an optional base64 payload the server stores in the private GCS bucket.
    """

    branch_id: UUID
    supplier_id: UUID
    warehouse_id: UUID | None = None
    supplier_reference: str | None = Field(None, max_length=100)
    notes: str | None = None
    # ~14M base64 chars ≈ a 10 MB file. Bounded so a huge payload cannot exhaust
    # worker memory when decoded.
    invoice_image_base64: str | None = Field(None, max_length=14_000_000)
    invoice_content_type: str | None = Field(None, max_length=100)
    items: list[PurchaseOrderLineInput] = Field(default_factory=list)
    misc_items: list[PurchaseOrderMiscLineInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_a_line(self):
        if not self.items and not self.misc_items:
            raise ValueError("A purchase order needs at least one line")
        return self


class PosInvoiceUpload(BaseModel):
    """Attach/replace a PO's invoice image from the till. The image rides as
    base64 in JSON (the till's API client is JSON-only, like create-and-receive),
    rather than as a raw multipart body the way the admin browser upload does."""

    # ~14M base64 chars ≈ a 10 MB file — bounded so a huge payload cannot exhaust
    # worker memory when decoded.
    invoice_image_base64: str = Field(max_length=14_000_000)
    invoice_content_type: str = Field(max_length=100)


class PurchaseOrderItemOption(BaseModel):
    """One inventory item that appears on at least one purchase order — the
    options for the "filter by item" picker on the PO list (admin and till)."""

    id: UUID
    name: str
    sku: str | None = None


class PurchaseOrderLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    received_quantity: Decimal
    outstanding_quantity: Decimal
    unit: str
    conversion_factor: Decimal
    entered_total: Decimal
    vat_amount: Decimal
    net_total: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    item_name: str | None = None
    item_sku: str | None = None
    #: The item's real purchase/stock unit (g, kg, piece …) — for display on the
    #: till's receive screen, which otherwise only has the abstract ``unit`` kind.
    storage_unit: str | None = None


class PurchaseOrderMiscLineResponse(ORMModel):
    id: UUID
    name: str
    quantity: Decimal
    storage_unit: str
    entered_total: Decimal
    vat_amount: Decimal
    net_total: Decimal
    unit_cost: Decimal


class PurchaseOrderResponse(ORMModel):
    id: UUID
    reference: str
    status: str
    origin: str
    supplier_id: UUID
    branch_id: UUID
    warehouse_id: UUID | None
    business_date: str
    delivery_date: date | None
    supplier_reference: str | None
    invoice_object_key: str | None
    additional_cost: Decimal
    subtotal_net: Decimal
    vat_total: Decimal
    total_gross: Decimal
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
    misc_items: list[PurchaseOrderMiscLineResponse] = []
    supplier_name: str | None = None
    #: Whether an invoice image is attached — cheap for list rows to render an
    #: indicator without signing a URL for every row.
    has_invoice: bool = False
    #: A short-lived signed URL for the invoice image. Filled only on the
    #: single-order read, never in a list (signing is a per-row IAM round-trip).
    invoice_url: str | None = None


class CostLayerResponse(ORMModel):
    id: UUID
    warehouse_id: UUID
    source_kind: str
    posting_sequence: int
    purchase_order_id: UUID | None
    original_quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal
    received_at: datetime
    warehouse_name: str | None = None
    #: Human reference for where this layer's stock (and its cost) came from —
    #: the PO number for a purchase, else the adjustment/reversal/count
    #: transaction reference (ADJ-…, CAD-…). Paired with ``received_at`` (date).
    source_reference: str | None = None
    #: remaining_quantity × unit_cost, quantised server-side — the layer's
    #: contribution to the item's on-hand value (the "math" the breakdown shows).
    line_value: Decimal | None = None
    #: When the stock was posted (the source transaction's posting time).
    posted_at: datetime | None = None
    #: The cost is still an estimate — this stock is waiting on its next priced
    #: receipt, which will re-cost it (and whatever was already used from it).
    cost_is_provisional: bool = False
    #: Where the *cost* came from when it is not this layer's own document —
    #: e.g. a count overage priced by the PO that followed it.
    cost_source_reference: str | None = None
    #: The layer the next issue will draw from.
    next_out: bool = False


class ItemCostLayersResponse(BaseModel):
    """The FIFO layers that make up an item's current stock, and its value."""

    item_id: UUID
    branch_id: UUID | None = None
    #: Σ remaining layers — the stock the cost breakdown covers.
    total_quantity: Decimal
    total_value: Decimal
    average_cost: Decimal
    #: Stock on hand per the ledger (Σ level quantity in scope).
    on_hand_quantity: Decimal = Decimal("0")
    #: The layers account for exactly the stock on hand (max(on hand, 0)).
    layers_match_stock: bool = True
    layers: list[CostLayerResponse] = []


class ItemCostHistoryRow(BaseModel):
    """One ledger line's effect on an item's quantity and value at a branch."""

    line_id: UUID
    transaction_id: UUID
    reference: str
    type: str
    posted_at: datetime | None
    business_date: str | None
    #: Signed storage quantity (+ in, − out).
    quantity: Decimal
    #: What the line is worth now, after any later price true-up.
    unit_cost: Decimal
    total_cost: Decimal
    #: What it was booked at when it posted, when that differs.
    booked_total_cost: Decimal | None = None
    is_provisional: bool
    superseded: bool
    cost_source_reference: str | None = None
    running_quantity: Decimal
    running_value: Decimal
    running_average_cost: Decimal | None


class ItemCostHistoryResponse(BaseModel):
    item_id: UUID
    branch_id: UUID
    items: list[ItemCostHistoryRow]
    total: int
    page: int
    per_page: int
    pages: int


class ReceiveLine(BaseModel):
    purchase_order_item_id: UUID
    #: What actually arrived for this line — 0 when the line was a full no-show
    #: (recorded as short). A line whose received quantity differs from what was
    #: ordered must carry a ``variance_reason``, the same as a transfer receipt.
    quantity: Decimal = Field(ge=0)
    variance_reason: str | None = None


class ReceivePurchaseOrderRequest(BaseModel):
    lines: list[ReceiveLine] = Field(min_length=1)


class VoidPurchaseOrderRequest(BaseModel):
    #: Why the order is being voided — recorded on the reversal transaction and
    #: shown on the cost-adjustment trail.
    reason: str = Field(min_length=1, max_length=500)


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


class ResetCostFromRecipeRequest(BaseModel):
    notes: str | None = None


class ResetCostFromRecipeResponse(BaseModel):
    item_id: str
    #: How many branch stock levels were revalued to their per-branch recipe cost.
    levels_adjusted: int
    #: Levels left untouched because the recipe priced to zero there (ingredients
    #: not costed at that branch) or the revaluation could not be applied.
    levels_skipped: int
    #: One entry per revalued level, each carrying that branch's new average cost.
    adjustments: list[CostAdjustmentResponse]


class QuantityAdjustmentRequest(BaseModel):
    branch_id: UUID
    item_id: UUID
    #: Signed: negative writes stock off.
    quantity_delta: Decimal
    reason_id: UUID | None = None
    notes: str | None = None


# ─── Transfer templates ───────────────────────────────────────────────────────


class TransferTemplateItemInput(BaseModel):
    item_id: UUID
    display_order: int = 0


class TransferTemplateUpsert(BaseModel):
    source_branch_id: UUID
    destination_branch_id: UUID | None = None
    name: str = Field(min_length=1, max_length=150)
    is_active: bool = True
    display_order: int = 0
    items: list[TransferTemplateItemInput] = Field(min_length=1)


class TransferTemplateItemResponse(ORMModel):
    id: UUID
    item_id: UUID
    display_order: int
    item_name: str | None = None
    item_sku: str | None = None
    #: The item's inventory category, backfilled from the loaded item so the
    #: create-transfer screen and template configurator can group by it. Both null
    #: for an uncategorised item (sort last).
    category_name: str | None = None
    category_order: int | None = None


class TransferTemplateResponse(ORMModel):
    id: UUID
    source_branch_id: UUID
    destination_branch_id: UUID | None
    name: str
    is_active: bool
    display_order: int
    #: The revision number within the (source_branch_id, name) lineage. The admin
    #: shows version history and marks the highest per lineage as Current.
    version_number: int
    items: list[TransferTemplateItemResponse] = []
