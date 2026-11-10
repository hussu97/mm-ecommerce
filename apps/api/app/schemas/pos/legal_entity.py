"""Legal entities — the trade licences orders are booked under."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from app.models.legal_entity import EMIRATES
from app.schemas.inventory import TradeLicenseAuthorityLiteral

from ._base import ORMModel

#: The seven emirates, derived from the model so the OpenAPI enum and the DB
#: CHECK cannot disagree.
EmirateLiteral = Literal[EMIRATES]  # type: ignore[valid-type]

_TIN = r"^[0-9]{10}$"
#: `0235:` + the 10-digit TIN for a UAE registrant; any other scheme:value pair
#: is accepted too (a foreign or group participant).
_PEPPOL = r"^[0-9]{4}:[A-Za-z0-9.\-]{1,46}$"


class _VatFields(BaseModel):
    vat_tin: str | None = Field(None, pattern=_TIN)
    peppol_id: str | None = Field(None, max_length=50, pattern=_PEPPOL)
    vat_issuing_authority: str | None = Field(None, max_length=120)
    vat_emirate: EmirateLiteral | None = None  # type: ignore[valid-type]
    vat_effective_date: date | None = None


class LegalEntityCreate(_VatFields):
    reference: str = Field(min_length=1, max_length=50)
    legal_name: str = Field(min_length=1, max_length=200)
    brand_name: str = Field(min_length=1, max_length=200)
    vat_registered: bool = True
    tax_number: str | None = Field(None, max_length=50)
    invoice_title: str = Field("Tax Invoice", min_length=1, max_length=120)
    logo_url: str | None = Field(None, max_length=500)
    is_active: bool = True
    registered_address: str | None = Field(None, max_length=1000)
    bank_name: str | None = Field(None, max_length=120)
    bank_account_name: str | None = Field(None, max_length=200)
    bank_account_number: str | None = Field(None, max_length=50)
    iban: str | None = Field(None, max_length=34)
    swift_code: str | None = Field(None, max_length=11)
    invoice_cc_emails: list[EmailStr] | None = None


class LegalEntityUpdate(_VatFields):
    """Every field optional. The trade-licence number/expiry are not here: they
    mirror the current licence cycle, edited under `/trade-licenses`."""

    legal_name: str | None = Field(None, min_length=1, max_length=200)
    brand_name: str | None = Field(None, min_length=1, max_length=200)
    vat_registered: bool | None = None
    tax_number: str | None = Field(None, max_length=50)
    invoice_title: str | None = Field(None, min_length=1, max_length=120)
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
    #: Mirror of the current licence cycle (the one expiring last).
    trade_license_number: str | None
    trade_license_expiry: date | None = None
    logo_url: str | None
    is_active: bool
    vat_tin: str | None = None
    peppol_id: str | None = None
    vat_issuing_authority: str | None = None
    vat_emirate: str | None = None
    vat_effective_date: date | None = None
    has_vat_certificate: bool = False
    vat_certificate_content_type: str | None = None
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


# ─── Trade-licence cycles ─────────────────────────────────────────────────────


class _TradeLicenseDates(BaseModel):
    @model_validator(mode="after")
    def _issue_before_expiry(self):
        issued = getattr(self, "issue_date", None)
        expires = getattr(self, "expiry_date", None)
        if issued is not None and expires is not None and issued > expires:
            raise ValueError("The issue date must be on or before the expiry date")
        return self


class TradeLicenseCreate(_TradeLicenseDates):
    license_number: str = Field(min_length=1, max_length=100)
    registration_number: str | None = Field(None, max_length=100)
    issuing_authority: TradeLicenseAuthorityLiteral  # type: ignore[valid-type]
    emirate: EmirateLiteral  # type: ignore[valid-type]
    issue_date: date | None = None
    expiry_date: date
    notes: str | None = Field(None, max_length=2000)


class TradeLicenseUpdate(_TradeLicenseDates):
    license_number: str | None = Field(None, min_length=1, max_length=100)
    registration_number: str | None = Field(None, max_length=100)
    issuing_authority: TradeLicenseAuthorityLiteral | None = None  # type: ignore[valid-type]
    emirate: EmirateLiteral | None = None  # type: ignore[valid-type]
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = Field(None, max_length=2000)


class TradeLicenseResponse(ORMModel):
    id: UUID
    legal_entity_id: UUID
    license_number: str
    registration_number: str | None
    issuing_authority: str
    emirate: str
    issue_date: date | None
    expiry_date: date
    notes: str | None
    has_document: bool
    document_content_type: str | None
    #: True for the cycle that expires last — the one mirrored onto the entity.
    is_current: bool = False
    created_at: datetime
    updated_at: datetime


class LegalEntityDocumentUrl(BaseModel):
    """A short-lived signed URL to one legal-entity document in the private bucket."""

    url: str
    content_type: str | None = None
