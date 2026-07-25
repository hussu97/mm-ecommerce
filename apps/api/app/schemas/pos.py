"""Pydantic schemas for the POS foundation entities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Translations = dict[str, dict[str, str]]

OrderTypeLiteral = Literal["dine_in", "pickup", "delivery", "drive_thru"]

_TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Taxes ────────────────────────────────────────────────────────────────────


class TaxCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    rate: Decimal = Field(ge=0, le=1, description="Fraction, e.g. 0.05 for 5%")
    type: Literal["inclusive", "exclusive"] = "inclusive"
    is_active: bool = True


class TaxUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    rate: Decimal | None = Field(None, ge=0, le=1)
    type: Literal["inclusive", "exclusive"] | None = None
    is_active: bool | None = None


class TaxResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    rate: Decimal
    type: str
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaxGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    tax_ids: list[UUID] = Field(default_factory=list)
    is_active: bool = True
    is_default: bool = False


class TaxGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    tax_ids: list[UUID] | None = None
    is_active: bool | None = None
    is_default: bool | None = None


class TaxGroupResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str | None
    is_active: bool
    is_default: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    taxes: list[TaxResponse] = []
    combined_rate: Decimal = Decimal("0")


# ─── Branches ─────────────────────────────────────────────────────────────────


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    translations: Translations = Field(default_factory=dict)
    reference: str = Field(min_length=1, max_length=50)
    type: Literal["restaurant", "kitchen", "warehouse"] = "restaurant"
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    phone: str | None = Field(None, max_length=30)
    address: str | None = None
    opening_from: str = Field("00:00", pattern=_TIME_RE)
    opening_to: str = Field("23:59", pattern=_TIME_RE)
    business_day_start: str = Field("04:00", pattern=_TIME_RE)
    inventory_end_of_day_time: str = Field("04:00", pattern=_TIME_RE)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    tax_registration_name: str | None = Field(None, max_length=200)
    tax_group_id: UUID | None = None
    receives_online_orders: bool = True
    accepts_reservations: bool = False
    reservation_duration: int = Field(60, ge=5, le=600)
    reservation_times: dict | None = None
    is_active: bool = True
    display_order: int = 0


class BranchUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    name_localized: str | None = Field(None, max_length=200)
    translations: Translations | None = None
    reference: str | None = Field(None, min_length=1, max_length=50)
    type: Literal["restaurant", "kitchen", "warehouse"] | None = None
    latitude: Decimal | None = Field(None, ge=-90, le=90)
    longitude: Decimal | None = Field(None, ge=-180, le=180)
    phone: str | None = Field(None, max_length=30)
    address: str | None = None
    opening_from: str | None = Field(None, pattern=_TIME_RE)
    opening_to: str | None = Field(None, pattern=_TIME_RE)
    business_day_start: str | None = Field(None, pattern=_TIME_RE)
    inventory_end_of_day_time: str | None = Field(None, pattern=_TIME_RE)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    tax_registration_name: str | None = Field(None, max_length=200)
    tax_group_id: UUID | None = None
    receives_online_orders: bool | None = None
    accepts_reservations: bool | None = None
    reservation_duration: int | None = Field(None, ge=5, le=600)
    reservation_times: dict | None = None
    is_active: bool | None = None
    display_order: int | None = None


class BranchResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str
    type: str
    latitude: Decimal | None
    longitude: Decimal | None
    phone: str | None
    address: str | None
    opening_from: str
    opening_to: str
    business_day_start: str
    inventory_end_of_day_time: str
    receipt_header: str | None
    receipt_footer: str | None
    tax_number: str | None
    tax_registration_name: str | None
    tax_group_id: UUID | None
    receives_online_orders: bool
    accepts_reservations: bool
    reservation_duration: int
    reservation_times: dict | None
    is_active: bool
    display_order: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BusinessDayResponse(ORMModel):
    id: UUID
    branch_id: UUID
    business_date: str
    opened_at: datetime
    closed_at: datetime | None
    opened_by_id: UUID | None
    closed_by_id: UUID | None
    total_sales: Decimal
    total_orders: int
    total_discounts: Decimal
    total_returns: Decimal
    total_taxes: Decimal
    is_open: bool


# ─── Payment methods ──────────────────────────────────────────────────────────


class PaymentMethodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    code: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    type: Literal["cash", "card", "gift_card", "house_account", "online", "other"]
    auto_open_drawer: bool = False
    allows_tendering: bool = False
    allows_tips: bool = False
    allows_refund: bool = True
    rounding_step: str | None = Field(None, max_length=10)
    is_active: bool = True
    display_order: int = 0


class PaymentMethodUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    code: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    type: (
        Literal["cash", "card", "gift_card", "house_account", "online", "other"] | None
    ) = None
    auto_open_drawer: bool | None = None
    allows_tendering: bool | None = None
    allows_tips: bool | None = None
    allows_refund: bool | None = None
    rounding_step: str | None = Field(None, max_length=10)
    is_active: bool | None = None
    display_order: int | None = None


class PaymentMethodResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    code: str
    type: str
    auto_open_drawer: bool
    allows_tendering: bool
    allows_tips: bool
    allows_refund: bool
    rounding_step: str | None
    is_active: bool
    display_order: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ─── Charges ──────────────────────────────────────────────────────────────────


class ChargeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    reference: str | None = Field(None, max_length=50)
    type: Literal["percentage", "fixed", "open"]
    value: Decimal = Field(Decimal("0"), ge=0)
    is_auto_applied: bool = False
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    tax_group_id: UUID | None = None
    is_active: bool = True

    @field_validator("value")
    @classmethod
    def _percentage_within_range(cls, v: Decimal, info) -> Decimal:
        if info.data.get("type") == "percentage" and v > 1:
            raise ValueError("percentage charges are fractions, e.g. 0.10 for 10%")
        return v


class ChargeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    reference: str | None = Field(None, max_length=50)
    type: Literal["percentage", "fixed", "open"] | None = None
    value: Decimal | None = Field(None, ge=0)
    is_auto_applied: bool | None = None
    order_types: list[OrderTypeLiteral] | None = None
    tax_group_id: UUID | None = None
    is_active: bool | None = None


class ChargeResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    reference: str | None
    type: str
    value: Decimal
    is_auto_applied: bool
    order_types: list[str]
    tax_group_id: UUID | None
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ─── Reasons ──────────────────────────────────────────────────────────────────

ReasonTypeLiteral = Literal["void_return", "quantity_adjustment", "drawer_operation"]


class ReasonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations = Field(default_factory=dict)
    type: ReasonTypeLiteral
    is_active: bool = True


class ReasonUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    name_localized: str | None = Field(None, max_length=150)
    translations: Translations | None = None
    type: ReasonTypeLiteral | None = None
    is_active: bool | None = None


class ReasonResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    type: str
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ─── Tags ─────────────────────────────────────────────────────────────────────

TagTypeLiteral = Literal[
    "order", "customer", "product", "inventory_item", "revenue_center"
]


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    #: Firing order — starters before mains before dessert.
    display_order: int = 0
    is_active: bool = True


class CourseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    display_order: int | None = None
    is_active: bool | None = None


class CourseResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations = {}
    display_order: int
    is_active: bool


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    type: TagTypeLiteral
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class TagUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    type: TagTypeLiteral | None = None
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class TagResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    type: str
    color: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TagAssignment(BaseModel):
    tag_ids: list[UUID]


# ─── Roles & staff ────────────────────────────────────────────────────────────


class PermissionEntry(BaseModel):
    slug: str
    description: str


class PermissionCatalogue(BaseModel):
    groups: dict[str, list[PermissionEntry]]


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    is_super_admin: bool = False
    is_active: bool = True


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    name_localized: str | None = Field(None, max_length=100)
    translations: Translations | None = None
    permissions: list[str] | None = None
    is_super_admin: bool | None = None
    is_active: bool | None = None


class RoleResponse(ORMModel):
    id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    permissions: list[str]
    is_super_admin: bool
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    user_count: int = 0


class StaffCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(min_length=1, max_length=150)
    staff_number: str | None = Field(None, max_length=30)
    phone: str | None = Field(None, max_length=20)
    password: str | None = Field(None, min_length=8, max_length=128)
    pin: str | None = Field(None, pattern=r"^\d{4,8}$")
    role_id: UUID | None = None
    branch_ids: list[UUID] = Field(default_factory=list)
    is_active: bool = True
    is_admin: bool = False
    is_driver: bool = False


class StaffUpdate(BaseModel):
    email: str | None = Field(None, min_length=3, max_length=255)
    display_name: str | None = Field(None, min_length=1, max_length=150)
    staff_number: str | None = Field(None, max_length=30)
    phone: str | None = Field(None, max_length=20)
    password: str | None = Field(None, min_length=8, max_length=128)
    pin: str | None = Field(None, pattern=r"^\d{4,8}$")
    role_id: UUID | None = None
    branch_ids: list[UUID] | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    is_driver: bool | None = None


class StaffResponse(ORMModel):
    id: UUID
    email: str
    display_name: str | None
    staff_number: str | None
    phone: str | None
    role_id: UUID | None
    role_name: str | None = None
    branch_ids: list[UUID] = []
    is_active: bool
    is_admin: bool
    is_staff: bool
    is_driver: bool
    has_pin: bool = False
    created_at: datetime
    updated_at: datetime


class PinLoginRequest(BaseModel):
    branch_id: UUID
    pin: str = Field(pattern=r"^\d{4,8}$")
    device_token: str | None = None


class PinLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    staff: StaffResponse
    permissions: list[str]


# ─── Devices & printers ───────────────────────────────────────────────────────

DeviceTypeLiteral = Literal["cashier", "sub_cashier", "kds", "display", "notifier"]


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    reference: str = Field(min_length=1, max_length=50)
    type: DeviceTypeLiteral
    branch_id: UUID
    category_ids: list[UUID] = Field(default_factory=list)


class DeviceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    reference: str | None = Field(None, min_length=1, max_length=50)
    type: DeviceTypeLiteral | None = None
    branch_id: UUID | None = None
    status: Literal["available", "used", "disabled"] | None = None
    category_ids: list[UUID] | None = None


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
    app_version: str | None
    os_version: str | None
    model_identifier: str | None
    category_ids: list[UUID]
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DevicePairRequest(BaseModel):
    pairing_code: str = Field(min_length=4, max_length=12)
    app_version: str | None = Field(None, max_length=30)
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


# ─── Kitchen flows ────────────────────────────────────────────────────────────


class KitchenFlowCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations = Field(default_factory=dict)
    order_types: list[OrderTypeLiteral] = Field(default_factory=list)
    category_ids: list[UUID] = Field(default_factory=list)
    is_default: bool = False
    auto_complete_seconds: int = Field(0, ge=0, le=86400)
    display_order: int = 0
    is_active: bool = True


class KitchenFlowUpdate(BaseModel):
    branch_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations | None = None
    order_types: list[OrderTypeLiteral] | None = None
    category_ids: list[UUID] | None = None
    is_default: bool | None = None
    auto_complete_seconds: int | None = Field(None, ge=0, le=86400)
    display_order: int | None = None
    is_active: bool | None = None


class KitchenFlowResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    order_types: list[str]
    category_ids: list[UUID] = []
    is_default: bool
    auto_complete_seconds: int
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ─── Sections & tables ────────────────────────────────────────────────────────


class SectionCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations = Field(default_factory=dict)
    layout: dict = Field(default_factory=dict)
    display_order: int = 0
    is_active: bool = True


class SectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    name_localized: str | None = Field(None, max_length=120)
    translations: Translations | None = None
    layout: dict | None = None
    display_order: int | None = None
    is_active: bool | None = None


class TableCreate(BaseModel):
    section_id: UUID
    name: str = Field(min_length=1, max_length=60)
    seats: int = Field(4, ge=1, le=100)
    accepts_reservations: bool = True
    revenue_center_tag_id: UUID | None = None
    layout: dict = Field(default_factory=dict)
    display_order: int = 0
    is_active: bool = True


class TableUpdate(BaseModel):
    section_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=60)
    seats: int | None = Field(None, ge=1, le=100)
    status: Literal["free", "occupied", "check_printed", "reserved"] | None = None
    accepts_reservations: bool | None = None
    parent_id: UUID | None = None
    revenue_center_tag_id: UUID | None = None
    layout: dict | None = None
    display_order: int | None = None
    is_active: bool | None = None


class TableResponse(ORMModel):
    id: UUID
    section_id: UUID
    name: str
    seats: int
    status: str
    accepts_reservations: bool
    parent_id: UUID | None
    revenue_center_tag_id: UUID | None
    layout: dict
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SectionResponse(ORMModel):
    id: UUID
    branch_id: UUID
    name: str
    name_localized: str | None
    translations: Translations
    layout: dict
    display_order: int
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tables: list[TableResponse] = []


# ─── Tills, shifts, drawer operations ─────────────────────────────────────────


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


class ShiftClockInRequest(BaseModel):
    branch_id: UUID
    user_id: UUID | None = None


class ShiftResponse(ORMModel):
    id: UUID
    branch_id: UUID
    user_id: UUID
    business_date: str
    clocked_in_at: datetime
    clocked_out_at: datetime | None
    duration_minutes: int | None
    created_at: datetime
    updated_at: datetime


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


# ─── Business settings ────────────────────────────────────────────────────────


class BusinessSettingsUpdate(BaseModel):
    business_name: str | None = Field(None, min_length=1, max_length=200)
    logo_url: str | None = Field(None, max_length=500)
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    currency_symbol: str | None = Field(None, max_length=8)
    decimal_places: int | None = Field(None, ge=0, le=4)
    timezone: str | None = Field(None, max_length=64)

    receipt_logo_url: str | None = Field(None, max_length=500)
    receipt_language_mode: Literal["main", "localized", "both"] | None = None
    receipt_main_language: str | None = Field(None, max_length=5)
    receipt_localized_language: str | None = Field(None, max_length=5)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    invoice_title: str | None = Field(None, max_length=120)
    receipt_show_order_number: bool | None = None
    receipt_show_calories: bool | None = None
    receipt_show_subtotal: bool | None = None
    receipt_show_rounding: bool | None = None
    receipt_show_closer_username: bool | None = None
    receipt_show_creator_username: bool | None = None
    receipt_show_check_number: bool | None = None
    receipt_hide_free_modifiers: bool | None = None
    receipt_show_pickup_phone: bool | None = None
    receipt_show_qr: bool | None = None

    kitchen_sorting: Literal["as_added", "by_category"] | None = None
    kitchen_show_default_modifiers: bool | None = None
    kitchen_auto_print_on_send: bool | None = None

    inventory_logo_url: str | None = Field(None, max_length=500)
    inventory_header: str | None = None
    inventory_footer: str | None = None
    prevent_negative_stock: bool | None = None

    require_customer_for_delivery: bool | None = None
    default_order_type: OrderTypeLiteral | None = None
    cash_rounding_step: Decimal | None = Field(None, ge=0, le=10)
    auto_logout_seconds: int | None = Field(None, ge=0, le=86400)
    order_number_reset_daily: bool | None = None
    enable_tips: bool | None = None
    extra: dict | None = None


class BusinessSettingsResponse(ORMModel):
    id: UUID
    business_name: str
    logo_url: str | None
    currency_code: str
    currency_symbol: str
    decimal_places: int
    timezone: str

    receipt_logo_url: str | None
    receipt_language_mode: str
    receipt_main_language: str
    receipt_localized_language: str
    receipt_header: str | None
    receipt_footer: str | None
    invoice_title: str
    receipt_show_order_number: bool
    receipt_show_calories: bool
    receipt_show_subtotal: bool
    receipt_show_rounding: bool
    receipt_show_closer_username: bool
    receipt_show_creator_username: bool
    receipt_show_check_number: bool
    receipt_hide_free_modifiers: bool
    receipt_show_pickup_phone: bool
    receipt_show_qr: bool

    kitchen_sorting: str
    kitchen_show_default_modifiers: bool
    kitchen_auto_print_on_send: bool

    inventory_logo_url: str | None
    inventory_header: str | None
    inventory_footer: str | None
    prevent_negative_stock: bool

    require_customer_for_delivery: bool
    default_order_type: str
    cash_rounding_step: Decimal
    auto_logout_seconds: int
    order_number_reset_daily: bool
    enable_tips: bool
    extra: dict
    created_at: datetime
    updated_at: datetime
