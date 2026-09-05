"""Public contracts for versioned recipes and inventory reconciliation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


OwnerKind = Literal["product", "modifier_option", "inventory_item"]


class VersionedRecipeLineInput(BaseModel):
    item_id: UUID
    quantity: Decimal = Field(gt=0)
    yield_percentage: Decimal = Field(Decimal("1"), gt=0, le=1)
    inactive_in_order_types: list[str] = Field(default_factory=list)
    display_order: int = 0
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class RecipeDraftRequest(BaseModel):
    ingredients: list[VersionedRecipeLineInput] = Field(min_length=1)
    source: str = Field("mm", max_length=30)
    source_payload_hash: str | None = Field(None, max_length=64)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class VersionedRecipeLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    ingredient_unit: str
    yield_percentage: Decimal
    inactive_in_order_types: list[str]
    display_order: int
    source_metadata: dict[str, Any]


class RecipeVersionResponse(ORMModel):
    id: UUID
    recipe_id: UUID
    version_number: int
    status: str
    source: str
    source_payload_hash: str | None
    source_metadata: dict[str, Any]
    activated_at: datetime | None
    activated_by: UUID | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime
    lines: list[VersionedRecipeLineResponse] = []


class VersionedRecipeResponse(ORMModel):
    id: UUID
    owner_kind: str
    product_id: UUID | None
    modifier_option_id: UUID | None
    inventory_item_id: UUID | None
    versions: list[RecipeVersionResponse] = []


class RecipeExpansionRequest(BaseModel):
    multiplier: Decimal = Field(Decimal("1"), gt=0)
    order_type: str | None = None


class ExpandedInventoryLine(BaseModel):
    item_id: UUID
    quantity: Decimal
    recipe_version_ids: list[UUID]
    paths: list[list[dict[str, str]]]


class RecipeExpansionResponse(BaseModel):
    lines: list[ExpandedInventoryLine]
    recipe_version_ids: list[UUID]


class BranchInventorySettingsUpdate(BaseModel):
    inventory_enabled: bool | None = None
    production_enabled: bool | None = None
    sales_consumption_enabled: bool | None = None
    validation_mode: bool | None = None
    allow_negative_stock: bool | None = None
    approval_cost_threshold: Decimal | None = Field(None, ge=0)
    approval_variance_percent: Decimal | None = Field(None, ge=0)


class BranchInventorySettingsResponse(ORMModel):
    id: UUID
    branch_id: UUID
    inventory_enabled: bool
    production_enabled: bool
    sales_consumption_enabled: bool
    validation_mode: bool
    allow_negative_stock: bool
    approval_cost_threshold: Decimal
    approval_variance_percent: Decimal
    go_live_sequence: int | None
    go_live_at: datetime | None


class ReverseTransactionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class OrderInventoryReturnRequest(BaseModel):
    disposition: Literal["restock", "waste", "no_inventory_effect"]
    proportion: Decimal = Field(Decimal("1"), gt=0, le=1)
    idempotency_key: str = Field(min_length=8, max_length=200)
    notes: str | None = Field(None, max_length=1000)


class ProjectionDriftResponse(BaseModel):
    item_id: UUID
    warehouse_id: UUID
    cached_quantity: Decimal
    ledger_quantity: Decimal
    cached_average_cost: Decimal
    ledger_average_cost: Decimal
    through_sequence: int | None


class StockAuditRowInput(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    counted_quantity: Decimal = Field(ge=0)
    unit: Literal["storage", "ingredient"] = "ingredient"
    remark: str | None = Field(None, max_length=1000)


class StockAuditRequest(BaseModel):
    branch_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    rows: list[StockAuditRowInput] = Field(min_length=1)


class StockAuditPreviewRow(BaseModel):
    sku: str
    item_id: UUID | None
    item_name: str | None
    unit: str
    expected_quantity: Decimal | None
    counted_quantity: Decimal
    delta_quantity: Decimal | None
    remark: str | None
    errors: list[str]


class StockAuditPreviewResponse(BaseModel):
    branch_id: UUID
    rows: list[StockAuditPreviewRow]
    valid: bool
    transaction_id: UUID | None = None


class ReportTemplateItemInput(BaseModel):
    item_id: UUID
    display_order: int = 0
    required_input: Literal[
        "physical_count", "production", "internal_use", "waste", "receipt"
    ] = "physical_count"


class ReportTemplateUpsert(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=160)
    report_type: Literal[
        "production", "finished_goods", "raw_materials", "packaging", "spot_check"
    ]
    cadence: Literal["per_till", "per_business_day", "ad_hoc"] = "per_till"
    is_required: bool = False
    is_active: bool = True
    configuration: dict[str, Any] = Field(default_factory=dict)
    approval_cost_threshold: Decimal | None = Field(None, ge=0)
    approval_variance_percent: Decimal | None = Field(None, ge=0)
    items: list[ReportTemplateItemInput] = Field(min_length=1)


class ReportTemplateItemResponse(ORMModel):
    id: UUID
    item_id: UUID
    display_order: int
    required_input: str


class ReportTemplateResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    report_type: str
    cadence: str
    is_required: bool
    is_active: bool
    version_number: int
    configuration: dict[str, Any]
    approval_cost_threshold: Decimal | None
    approval_variance_percent: Decimal | None
    items: list[ReportTemplateItemResponse] = []


class ReportLineSave(BaseModel):
    item_id: UUID
    entered_quantity: Decimal | None = Field(None, ge=0)
    confirmed: bool = False
    override_reason: str | None = None


class ReportSaveRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    base_posting_sequence: int | None = None
    notes: str | None = None
    lines: list[ReportLineSave]


class ReportActionRequest(BaseModel):
    reason: str | None = Field(None, max_length=1000)


class ShiftReportLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    unit: str
    opening_quantity: Decimal
    purchasing_quantity: Decimal
    transfer_in_quantity: Decimal
    production_quantity: Decimal
    sales_consumption_quantity: Decimal
    production_consumption_quantity: Decimal
    transfer_out_quantity: Decimal
    waste_quantity: Decimal
    internal_use_quantity: Decimal
    expected_quantity: Decimal
    entered_quantity: Decimal | None
    confirmed: bool
    variance_quantity: Decimal | None
    variance_cost: Decimal | None
    override_reason: str | None
    source_summary: dict[str, Any]


class ShiftReportResponse(ORMModel):
    id: UUID
    template_id: UUID
    branch_id: UUID
    till_id: UUID | None
    business_date: str
    status: str
    idempotency_key: str
    template_snapshot: dict[str, Any]
    base_posting_sequence: int | None
    notes: str | None
    deferred_reason: str | None
    submitted_by: UUID | None
    approved_by: UUID | None
    submitted_at: datetime | None
    approved_at: datetime | None
    transaction_id: UUID | None
    lines: list[ShiftReportLineResponse] = []


class OrderInventoryMovementLine(BaseModel):
    item_id: UUID
    item_name: str
    item_sku: str
    unit: str
    signed_quantity: Decimal
    balance_after_quantity: Decimal | None
    recipe_version_id: UUID | None
    recipe_path: list[dict[str, Any]]


class OrderInventoryMovement(BaseModel):
    id: UUID
    reference: str
    type: str
    posting_sequence: int | None
    posted_at: datetime | None
    lines: list[OrderInventoryMovementLine]


class OrderInventorySourceEvent(BaseModel):
    id: UUID
    accepted_sequence: int
    status: str
    error_code: str | None
    error_detail: str | None


class OrderInventoryConsumptionResponse(BaseModel):
    order_id: UUID
    transactions: list[OrderInventoryMovement]
    source_events: list[OrderInventorySourceEvent]
    theoretical_plan: dict[str, Any] | None
    warnings: list[str]
