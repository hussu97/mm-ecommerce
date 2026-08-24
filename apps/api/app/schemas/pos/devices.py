"""Terminals and the printers attached to them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import ORMModel
from .branches import BranchResponse

DeviceTypeLiteral = Literal["cashier", "sub_cashier", "display", "notifier"]


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    reference: str = Field(min_length=1, max_length=50)
    type: DeviceTypeLiteral
    branch_id: UUID
    category_ids: list[UUID] = Field(default_factory=list)
    #: Online orders for this branch land on this terminal.
    #: Take them without waiting for somebody to press Accept — for a
    #: kitchen-only site where nobody is watching the iPad.
    auto_accept_online_orders: bool = False


class DeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    reference: str | None = Field(None, min_length=1, max_length=50)
    type: DeviceTypeLiteral | None = None
    branch_id: UUID | None = None
    status: Literal["available", "used", "disabled"] | None = None
    category_ids: list[UUID] | None = None
    #: Online orders for this branch land on this terminal.
    #: Take them without waiting for somebody to press Accept — for a
    #: kitchen-only site where nobody is watching the iPad.
    auto_accept_online_orders: bool = False


class DeviceResponse(ORMModel):
    id: UUID
    name: str
    reference: str
    type: str
    branch_id: UUID
    status: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    last_seen_at: datetime | None
    #: Refreshed from the `X-App-*` headers on every request the terminal makes,
    #: so these describe what it is running now rather than what it was paired
    #: with. Null on a terminal whose build predates those headers.
    app_version: str | None
    build_number: str | None
    platform: str | None
    os_version: str | None
    model_identifier: str | None
    category_ids: list[UUID]
    #: Online orders for this branch land on this terminal.
    #: Take them without waiting for somebody to press Accept — for a
    #: kitchen-only site where nobody is watching the iPad.
    auto_accept_online_orders: bool = False
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DevicePairRequest(BaseModel):
    pairing_code: str = Field(min_length=4, max_length=12)
    app_version: str | None = Field(None, max_length=30)
    #: Sent at pairing as well as on every later request, so a freshly paired
    #: terminal is complete in the console immediately rather than blank until
    #: its first authenticated call.
    build_number: str | None = Field(None, max_length=20)
    platform: str | None = Field(None, max_length=10)
    os_version: str | None = Field(None, max_length=30)
    model_identifier: str | None = Field(None, max_length=60)


class DevicePairResponse(BaseModel):
    device: DeviceResponse
    device_token: str
    branch: BranchResponse


class DeviceSessionResponse(BaseModel):
    """
    What a terminal needs to come back up knowing only its device token.

    The branch travels with the heartbeat because no cashier is signed in at
    that point, and every branch-scoped endpoint requires a user token.
    """

    device: DeviceResponse
    branch: BranchResponse


class PrinterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    branch_id: UUID
    device_id: UUID | None = None
    role: Literal["receipt", "kitchen", "label", "report"] = "receipt"
    connection: Literal["lan", "bluetooth", "usb", "airprint", "cloud"] = "lan"
    ip_address: str | None = Field(None, max_length=45)
    port: int = Field(9100, ge=1, le=65535)
    identifier: str | None = Field(None, max_length=120)
    paper_width_mm: int = Field(80, ge=40, le=120)
    characters_per_line: int = Field(48, ge=20, le=96)
    codepage: str = Field("cp864", max_length=20)
    supports_arabic: bool = True
    cut_after_print: bool = True
    has_cash_drawer: bool = False
    copies: int = Field(1, ge=1, le=5)
    kitchen_flow_id: UUID | None = None
    is_default: bool = False
    is_active: bool = True


class PrinterUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    branch_id: UUID | None = None
    device_id: UUID | None = None
    role: Literal["receipt", "kitchen", "label", "report"] | None = None
    connection: Literal["lan", "bluetooth", "usb", "airprint", "cloud"] | None = None
    ip_address: str | None = Field(None, max_length=45)
    port: int | None = Field(None, ge=1, le=65535)
    identifier: str | None = Field(None, max_length=120)
    paper_width_mm: int | None = Field(None, ge=40, le=120)
    characters_per_line: int | None = Field(None, ge=20, le=96)
    codepage: str | None = Field(None, max_length=20)
    supports_arabic: bool | None = None
    cut_after_print: bool | None = None
    has_cash_drawer: bool | None = None
    copies: int | None = Field(None, ge=1, le=5)
    kitchen_flow_id: UUID | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class PrinterResponse(ORMModel):
    id: UUID
    name: str
    branch_id: UUID
    device_id: UUID | None
    role: str
    connection: str
    ip_address: str | None
    port: int
    identifier: str | None
    paper_width_mm: int
    characters_per_line: int
    codepage: str
    supports_arabic: bool
    cut_after_print: bool
    has_cash_drawer: bool
    copies: int
    kitchen_flow_id: UUID | None
    is_default: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
