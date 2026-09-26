"""Legal entities — the trade licences orders are booked under."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from ._base import ORMModel


class LegalEntityCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=50)
    legal_name: str = Field(min_length=1, max_length=200)
    brand_name: str = Field(min_length=1, max_length=200)
    vat_registered: bool = True
    tax_number: str | None = Field(None, max_length=50)
    invoice_title: str = Field("Tax Invoice", min_length=1, max_length=120)
    trade_license_number: str | None = Field(None, max_length=100)
    logo_url: str | None = Field(None, max_length=500)
    is_active: bool = True
    registered_address: str | None = Field(None, max_length=1000)
    bank_name: str | None = Field(None, max_length=120)
    bank_account_name: str | None = Field(None, max_length=200)
    bank_account_number: str | None = Field(None, max_length=50)
    iban: str | None = Field(None, max_length=34)
    swift_code: str | None = Field(None, max_length=11)
    invoice_cc_emails: list[EmailStr] | None = None


class LegalEntityUpdate(BaseModel):
    legal_name: str | None = Field(None, min_length=1, max_length=200)
    brand_name: str | None = Field(None, min_length=1, max_length=200)
    vat_registered: bool | None = None
    tax_number: str | None = Field(None, max_length=50)
    invoice_title: str | None = Field(None, min_length=1, max_length=120)
    trade_license_number: str | None = Field(None, max_length=100)
    logo_url: str | None = Field(None, max_length=500)
    is_active: bool | None = None
    registered_address: str | None = Field(None, max_length=1000)
    bank_name: str | None = Field(None, max_length=120)
    bank_account_name: str | None = Field(None, max_length=200)
    bank_account_number: str | None = Field(None, max_length=50)
    iban: str | None = Field(None, max_length=34)
    swift_code: str | None = Field(None, max_length=11)
    invoice_cc_emails: list[EmailStr] | None = None


class LegalEntityResponse(ORMModel):
    id: UUID
    reference: str
    legal_name: str
    brand_name: str
    vat_registered: bool
    tax_number: str | None
    invoice_title: str
    trade_license_number: str | None
    logo_url: str | None
    is_active: bool
    registered_address: str | None = None
    bank_name: str | None = None
    bank_account_name: str | None = None
    bank_account_number: str | None = None
    iban: str | None = None
    swift_code: str | None = None
    invoice_cc_emails: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class OrderLegalEntity(ORMModel):
    """The subset of a legal entity a receipt / order reader needs — served
    nested on the order so the till prints the brand, TRN, title and logo, and
    the admin shows who the order was issued under."""

    id: UUID
    reference: str
    legal_name: str
    brand_name: str
    vat_registered: bool
    tax_number: str | None
    invoice_title: str
    logo_url: str | None
