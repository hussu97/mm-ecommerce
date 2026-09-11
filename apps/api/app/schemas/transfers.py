"""Schemas for the transfer domain: a parent transfer order and its per-branch
child transfers.

An admin raises one :class:`TransferOrderCreate` from a single source branch,
allocating quantities to several destination branches at once. It fans out into
one child transfer per destination; the parent totals across them. No stock
moves at create time — each child is shipped from the source till later, which
is where the movement (and the receive) happens.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Create (admin) ───────────────────────────────────────────────────────────


class TransferAllocationInput(BaseModel):
    """How much of one item goes to one destination branch."""

    branch_id: UUID
    quantity: Decimal = Field(gt=0)


class TransferOrderItemInput(BaseModel):
    item_id: UUID
    unit: Literal["storage", "ingredient"] = "storage"
    #: When the total allocated across branches exceeds the source's on-hand for
    #: this item, the admin must set this to override — a shortfall top-up
    #: adjustment is posted to the source at create time (see
    #: ``transfer_service.create_transfer_order``). Without it the create is
    #: refused, naming the shortfall.
    override: bool = False
    notes: str | None = None
    allocations: list[TransferAllocationInput] = Field(min_length=1)


class TransferOrderCreate(BaseModel):
    source_branch_id: UUID
    kind: Literal["transfer", "return"] = "transfer"
    source_warehouse_id: UUID | None = None
    required_date: date | None = None
    notes: str | None = None
    #: The template this order was raised from, if any — snapshotted for provenance.
    template_id: UUID | None = None
    #: A stable token so a retried create does not raise the fan-out twice.
    client_request_id: str | None = Field(None, max_length=64)
    items: list[TransferOrderItemInput] = Field(min_length=1)


# ─── Receive (source/destination till + admin) ────────────────────────────────


class TransferReceiveLine(BaseModel):
    #: The child transfer line (``TransferLine``) being booked in.
    transfer_line_id: UUID
    received_quantity: Decimal = Field(ge=0)
    #: Why the received quantity differs from what was sent (short/over).
    reason: str | None = None


class TransferReceive(BaseModel):
    lines: list[TransferReceiveLine] = Field(default_factory=list)


# ─── Responses ────────────────────────────────────────────────────────────────


class TransferLineResponse(ORMModel):
    id: UUID
    item_id: UUID
    quantity: Decimal
    approved_quantity: Decimal | None
    sent_quantity: Decimal
    received_quantity: Decimal
    unit: str
    notes: str | None
    variance_reason: str | None = None
    item_name: str | None = None
    item_sku: str | None = None
    #: The item's inventory category, backfilled from the loaded item so a client
    #: can group the lines by it. Both null for an uncategorised item (sort last).
    category_name: str | None = None
    category_order: int | None = None


class TransferResponse(ORMModel):
    """One source→destination leg of a transfer order."""

    id: UUID
    transfer_order_id: UUID
    reference: str
    status: str
    kind: str
    #: Destination.
    branch_id: UUID
    source_branch_id: UUID
    business_date: str
    notes: str | None
    sent_transaction_id: UUID | None
    received_transaction_id: UUID | None
    created_at: datetime
    items: list[TransferLineResponse] = []


class TransferOrderTotalLine(BaseModel):
    """One row of the parent's total-across-branches summary."""

    item_id: UUID
    item_name: str | None = None
    item_sku: str | None = None
    unit: str
    total_quantity: Decimal
    category_name: str | None = None
    category_order: int | None = None


class TransferOrderResponse(ORMModel):
    id: UUID
    reference: str
    status: str
    kind: str
    source_branch_id: UUID
    business_date: str
    required_date: date | None
    notes: str | None
    #: Set when the admin overrode on-hand at create; groups the shortfall
    #: top-up adjustments this order posted (see the adjustment report).
    adjustment_group_id: UUID | None = None
    template_id: UUID | None = None
    template_version: int | None = None
    created_at: datetime
    #: Totals rolled up across every child, one row per item.
    total_by_item: list[TransferOrderTotalLine] = []
    children: list[TransferResponse] = []


# ─── Parent report ────────────────────────────────────────────────────────────


class TransferOrderReportChild(BaseModel):
    transfer_id: UUID
    reference: str
    destination_branch_id: UUID
    destination_branch_name: str | None = None
    status: str
    item_count: int
    total_sent: Decimal
    total_received: Decimal
    sent_value: Decimal
    received_value: Decimal


class TransferOrderAdjustmentEntry(BaseModel):
    """One shortfall top-up posted when the admin overrode on-hand at create."""

    transaction_id: UUID
    transaction_reference: str
    item_id: UUID
    item_name: str | None = None
    quantity: Decimal
    unit_cost: Decimal
    value: Decimal


class TransferOrderReport(BaseModel):
    id: UUID
    reference: str
    status: str
    kind: str
    source_branch_id: UUID
    source_branch_name: str | None = None
    business_date: str
    created_at: datetime
    children: list[TransferOrderReportChild] = []
    #: The mini stock-adjustment report — empty unless the order overrode on-hand.
    adjustments: list[TransferOrderAdjustmentEntry] = []
    adjustment_total: Decimal = Decimal("0")
