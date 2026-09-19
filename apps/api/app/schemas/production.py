"""Schemas for the production side of a transfer and production order.

An admin raises production lines at a source branch (alongside a transfer, or on
their own). No stock moves at create; the source till produces each line later —
posting its PRODUCTION movement then — or cancels it with a note. The admin
Production Report tab reads these back.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Create (admin, production-only) ──────────────────────────────────────────


class ProductionOrderItemInput(BaseModel):
    item_id: UUID
    quantity: Decimal = Field(gt=0)
    unit: str = "storage"


class ProductionOrderCreate(BaseModel):
    source_branch_id: UUID
    notes: str | None = None
    client_request_id: str | None = Field(None, max_length=64)
    items: list[ProductionOrderItemInput] = Field(min_length=1)


# ─── POS actions ──────────────────────────────────────────────────────────────


class ProduceLineRequest(BaseModel):
    """Mark a line produced. ``quantity`` overrides the planned amount when the
    till adjusts it; omitted, the planned quantity is produced."""

    quantity: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class CancelLineRequest(BaseModel):
    note: str = Field(min_length=1)


# ─── Responses ────────────────────────────────────────────────────────────────


class ProductionLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    planned_quantity: Decimal
    produced_quantity: Decimal | None
    unit: str
    status: str
    cancel_note: str | None = None
    production_transaction_id: UUID | None = None
    produced_at: datetime | None = None
    item_name: str | None = None
    item_sku: str | None = None
    category_name: str | None = None
    category_order: int | None = None
    #: The posted PRODUCTION movement's reference, backfilled for the report.
    production_reference: str | None = None


class ProductionOrderResponse(ORMModel):
    id: UUID
    reference: str
    status: str
    source_branch_id: UUID
    source_branch_name: str | None = None
    transfer_order_id: UUID | None = None
    business_date: str
    notes: str | None
    auto_printed_at: datetime | None = None
    created_at: datetime
    lines: list[ProductionLineResponse] = []


class ProductionOrderSummary(ORMModel):
    """One row of the admin Production Report list."""

    id: UUID
    reference: str
    status: str
    source_branch_id: UUID
    source_branch_name: str | None = None
    business_date: str
    created_at: datetime
    line_count: int = 0
    produced_count: int = 0
    cancelled_count: int = 0
    pending_count: int = 0
