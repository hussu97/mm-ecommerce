"""Branches, their holidays and their business days."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from ._base import _TIME_RE, ORMModel, Translations


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
    address_localized: str | None = None
    city: str | None = Field(None, max_length=100)
    city_localized: str | None = Field(None, max_length=100)
    opening_from: str = Field("00:00", pattern=_TIME_RE)
    opening_to: str = Field("23:59", pattern=_TIME_RE)
    business_day_start: str = Field("04:00", pattern=_TIME_RE)
    inventory_end_of_day_time: str = Field("04:00", pattern=_TIME_RE)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    tax_registration_name: str | None = Field(None, max_length=200)
    tax_group_id: UUID | None = None
    #: What noon Send calls this branch. Null means it cannot dispatch through
    #: them — which is every branch until one is registered.
    noon_send_outlet_code: str | None = Field(None, max_length=50)
    noon_send_outlet_address_code: str | None = Field(None, max_length=120)
    receives_online_orders: bool = True
    #: Whether this branch handles cash / has a till drawer. A cashless kitchen
    #: skips the opening-float entry and the close-time cash count.
    cash_enabled: bool = True
    #: Whether a customer may choose to collect from here. False by default —
    #: a kitchen that bakes website orders is not automatically a counter.
    offers_pickup: bool = False
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
    address_localized: str | None = None
    city: str | None = Field(None, max_length=100)
    city_localized: str | None = Field(None, max_length=100)
    opening_from: str | None = Field(None, pattern=_TIME_RE)
    opening_to: str | None = Field(None, pattern=_TIME_RE)
    business_day_start: str | None = Field(None, pattern=_TIME_RE)
    inventory_end_of_day_time: str | None = Field(None, pattern=_TIME_RE)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    tax_number: str | None = Field(None, max_length=50)
    tax_registration_name: str | None = Field(None, max_length=200)
    tax_group_id: UUID | None = None
    noon_send_outlet_code: str | None = Field(None, max_length=50)
    noon_send_outlet_address_code: str | None = Field(None, max_length=120)
    receives_online_orders: bool | None = None
    cash_enabled: bool | None = None
    offers_pickup: bool | None = None
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
    address_localized: str | None
    city: str | None
    city_localized: str | None
    opening_from: str
    opening_to: str
    business_day_start: str
    inventory_end_of_day_time: str
    receipt_header: str | None
    receipt_footer: str | None
    tax_number: str | None
    tax_registration_name: str | None
    tax_group_id: UUID | None
    noon_send_outlet_code: str | None
    noon_send_outlet_address_code: str | None
    receives_online_orders: bool
    cash_enabled: bool
    offers_pickup: bool
    accepts_reservations: bool
    reservation_duration: int
    reservation_times: dict | None
    is_active: bool
    display_order: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BranchHolidayBase(BaseModel):
    """
    A whole day the branch does not trade.

    `holiday_date` is `YYYY-MM-DD` and the pattern is enforced here as well as
    by the CHECK on the column: these strings are compared against
    `date.isoformat()` output, so one written any other way would close the
    branch on no day at all, silently and without an error anywhere.

    No hours. A branch that opens late is a trading-hours change; a holiday is
    the day being gone.
    """

    holiday_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str = Field(min_length=1, max_length=120)
    note: str | None = Field(None, max_length=2000)


class BranchHolidayCreate(BranchHolidayBase):
    pass


class BranchHolidayUpdate(BaseModel):
    holiday_date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str | None = Field(None, min_length=1, max_length=120)
    note: str | None = Field(None, max_length=2000)


class BranchHolidayResponse(ORMModel):
    id: UUID
    branch_id: UUID
    holiday_date: str
    name: str
    note: str | None
    created_at: datetime
    updated_at: datetime


class WeeklyShift(BaseModel):
    """One open shift. weekday 0=Sunday … 6=Saturday; times HH:MM."""

    weekday: int = Field(ge=0, le=6)
    opens: str = Field(pattern=_TIME_RE)
    closes: str = Field(pattern=_TIME_RE)


class WeeklyHoursUpdate(BaseModel):
    """Replace a branch's whole weekly schedule (a weekday with no shift = closed).

    At most one shift per weekday — the model is one continuous shift a day.
    """

    shifts: list[WeeklyShift]

    @model_validator(mode="after")
    def _one_shift_per_day(self) -> "WeeklyHoursUpdate":
        seen: set[int] = set()
        for s in self.shifts:
            if s.weekday in seen:
                raise ValueError(
                    f"weekday {s.weekday} has more than one shift — one shift per day"
                )
            seen.add(s.weekday)
        return self


class WeeklyHoursResponse(BaseModel):
    branch_id: str
    shifts: list[WeeklyShift]


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
