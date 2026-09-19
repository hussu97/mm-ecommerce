"""Schemas for report actions triggered from the console."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field, field_validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"


class DailySalesEmailRequest(BaseModel):
    """Send the daily sales spreadsheet for a window to a set of recipients."""

    date_from: str = Field(pattern=_DATE)
    date_to: str = Field(pattern=_DATE)
    recipients: list[EmailStr] = Field(min_length=1)

    @field_validator("date_to")
    @classmethod
    def _in_order(cls, value: str, info) -> str:
        start = info.data.get("date_from")
        if start and start > value:
            raise ValueError("date_from must not be after date_to")
        return value


class DailySalesEmailRecipientResult(BaseModel):
    recipient: str
    status: str
    error: str | None = None


class DailySalesEmailResponse(BaseModel):
    subject: str
    rows: int
    sent: list[DailySalesEmailRecipientResult]


class VatLedgerRow(BaseModel):
    """One (legal entity, category, direction) total for the report window."""

    legal_entity_id: uuid.UUID
    legal_entity_name: str
    vat_registered: bool
    category: str
    direction: str
    net_value: Decimal
    vat_amount: Decimal
    gross_value: Decimal
    #: False on an input row whose entity is not VAT-registered — cost visible,
    #: VAT not reclaimable.
    vat_recoverable: bool
    source_count: int


class VatLedgerEntitySummary(BaseModel):
    """Rolled-up VAT position for one legal entity over the window."""

    legal_entity_id: uuid.UUID
    legal_entity_name: str
    vat_registered: bool
    #: Output VAT collected (net of refunds).
    output_vat: Decimal
    #: Input VAT that is actually recoverable.
    input_vat_recoverable: Decimal
    #: output_vat − input_vat_recoverable: what the entity owes the FTA (positive)
    #: or reclaims (negative).
    net_vat_position: Decimal


class VatLedgerResponse(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    rows: list[VatLedgerRow]
    summary: list[VatLedgerEntitySummary]
