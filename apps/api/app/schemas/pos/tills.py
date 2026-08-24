"""Tills, shifts and drawer operations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel


class TillOpenRequest(BaseModel):
    branch_id: UUID
    device_id: UUID | None = None
    opening_amount: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None


class TillCloseRequest(BaseModel):
    closing_amount: Decimal = Field(ge=0)
    notes: str | None = None


class TillResponse(ORMModel):
    id: UUID
    branch_id: UUID
    device_id: UUID | None
    user_id: UUID
    business_date: str
    status: str
    opened_at: datetime
    closed_at: datetime | None
    closed_by_id: UUID | None
    opening_amount: Decimal
    closing_amount: Decimal | None
    estimated_cash: Decimal
    variance: Decimal
    totals: dict
    notes: str | None
    created_at: datetime
    updated_at: datetime


class TillReport(BaseModel):
    """X-report (till still open) or Z-report (till closed)."""

    till_id: UUID
    branch_id: UUID
    business_date: str
    user_id: UUID
    opened_at: datetime
    closed_at: datetime | None
    opening_amount: Decimal
    estimated_cash: Decimal
    closing_amount: Decimal | None
    variance: Decimal | None
    orders_count: int
    gross_sales: Decimal
    discounts: Decimal
    returns: Decimal
    charges: Decimal
    taxes: Decimal
    net_sales: Decimal
    tips: Decimal
    payments_by_method: dict[str, Decimal]
    drawer_totals: dict[str, Decimal]


DrawerOperationTypeLiteral = Literal[
    "pay_in", "pay_out", "cash_drop", "open_drawer", "sales", "return"
]


class DrawerOperationCreate(BaseModel):
    type: DrawerOperationTypeLiteral
    amount: Decimal = Field(Decimal("0"), ge=0)
    reason_id: UUID | None = None
    notes: str | None = None


class DrawerOperationResponse(ORMModel):
    id: UUID
    till_id: UUID
    user_id: UUID
    type: str
    amount: Decimal
    reason_id: UUID | None
    order_id: UUID | None
    notes: str | None
    recorded_at: datetime
    created_at: datetime
