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


# ─── Producible-item basis metadata (admin grid) ──────────────────────────────


class ProducibleItemBasis(BaseModel):
    """One producible item's recipe basis, for the admin transfer/production grid.

    The grid renders the "qty to produce" cell in this basis — batches for a batch
    recipe, units otherwise — and shows the live "= N units" conversion from
    ``batch_yield``. Items with an active v2 recipe are listed; anything absent
    (legacy BOM, non-producible) is treated as unit basis by the client.
    """

    item_id: UUID
    basis: str = "unit"
    batch_yield: Decimal | None = None


# ─── Create (admin, production-only) ──────────────────────────────────────────


class ProductionOrderItemInput(BaseModel):
    #: ``quantity`` is in the item's *recipe basis* — batches for a batch-basis
    #: recipe, units otherwise. The service reads the item's active recipe version,
    #: snapshots the basis/yield onto the line, and stores the owner-unit truth on
    #: ``planned_quantity``. For a unit-basis item the two are identical, so an
    #: owner-unit caller is unaffected.
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
    till adjusts it, in the line's *recipe basis* (batches for a batch line);
    omitted, the planned quantity is produced. The service converts to owner units
    before posting the movement."""

    quantity: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class CancelLineRequest(BaseModel):
    note: str = Field(min_length=1)


# ─── Responses ────────────────────────────────────────────────────────────────


class ProductionLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    #: Owner-unit truth (what the ledger and inventory reports show).
    planned_quantity: Decimal
    produced_quantity: Decimal | None
    #: Recipe basis this line was raised in ('unit'/'batch') and, for a batch
    #: line, the owner units one batch makes. Drive the produce UI + printout.
    basis: str = "unit"
    batch_yield: Decimal | None = None
    #: The quantity in the line's basis (batches/units) — falls back to the
    #: owner-unit quantity on legacy rows raised before basis existed.
    planned_basis_quantity: Decimal | None = None
    produced_basis_quantity: Decimal | None = None
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
