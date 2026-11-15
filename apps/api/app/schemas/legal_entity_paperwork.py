"""A legal entity's other registration documents and its other files.

The trade licence and VAT certificate have their own shapes (see
`app.schemas.pos.legal_entity`); these are the rest — establishment card,
labour company card, chamber membership, Corporate Tax, MoA and the
the shop's premises documents — plus free-form files.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.legal_entity import (
    LEGAL_ENTITY_DOCUMENT_SPECS,
    LEGAL_ENTITY_DOCUMENT_TYPES,
)
from app.schemas.employee import EmployeeFileCreate, EmployeeFileUpdate

EntityDocTypeLiteral = Literal[LEGAL_ENTITY_DOCUMENT_TYPES]  # type: ignore[valid-type]


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _clean_details(value: dict[str, str | None] | None) -> dict[str, str] | None:
    """Drop blanks; the per-type key check happens where the type is known."""
    if value is None:
        return None
    out: dict[str, str] = {}
    for key, raw in value.items():
        text = (raw or "").strip()
        if text:
            out[key] = text[:300]
    return out


class _Dates(BaseModel):
    @model_validator(mode="after")
    def _issue_before_expiry(self):
        issued = getattr(self, "issue_date", None)
        expires = getattr(self, "expiry_date", None)
        if issued is not None and expires is not None and issued > expires:
            raise ValueError("The issue date must be on or before the expiry date")
        return self


class LegalEntityDocumentCreate(_Dates):
    doc_type: EntityDocTypeLiteral  # type: ignore[valid-type]
    document_number: str | None = Field(None, max_length=100)
    issue_date: date | None = None
    #: Required unless the type is issued once (Corporate Tax, MoA).
    expiry_date: date | None = None
    issuing_authority: str | None = Field(None, max_length=120)
    details: dict[str, str | None] = {}
    notes: str | None = Field(None, max_length=2000)

    @field_validator("details")
    @classmethod
    def _clean(cls, value):
        return _clean_details(value)

    @model_validator(mode="after")
    def _fits_the_type(self):
        spec = LEGAL_ENTITY_DOCUMENT_SPECS[self.doc_type]
        unknown = sorted(set(self.details) - set(spec["details"]))
        if unknown:
            raise ValueError(f"Unknown {self.doc_type} field(s): {', '.join(unknown)}")
        if spec["expires"] and self.expiry_date is None:
            raise ValueError("The expiry date is required")
        if not spec["expires"]:
            self.expiry_date = None
        return self


class LegalEntityDocumentUpdate(_Dates):
    """`doc_type` is fixed once created; the per-type checks run in the service."""

    document_number: str | None = Field(None, max_length=100)
    issue_date: date | None = None
    expiry_date: date | None = None
    issuing_authority: str | None = Field(None, max_length=120)
    details: dict[str, str | None] | None = None
    notes: str | None = Field(None, max_length=2000)

    @field_validator("details")
    @classmethod
    def _clean(cls, value):
        return _clean_details(value)


class LegalEntityDocumentResponse(_ORM):
    id: UUID
    legal_entity_id: UUID
    doc_type: str
    document_number: str | None
    issue_date: date | None
    expiry_date: date | None
    issuing_authority: str | None
    details: dict[str, str]
    notes: str | None
    has_file: bool
    file_content_type: str | None
    #: The current cycle for its type.
    is_current: bool = False
    created_at: datetime
    updated_at: datetime


class LegalEntityDocumentTypeSpec(BaseModel):
    doc_type: str
    label: str
    #: The console's section for it ("Visas & labour", "Registration", "Premises").
    group: str
    #: One line on what it is and why it matters.
    hint: str
    number_label: str
    authority_label: str
    #: Suggestions for the issuer box.
    authorities: list[str]
    #: False for a document issued once (no expiry date).
    expires: bool
    #: Raises the expiry warning when it lapses.
    expiry_tracked: bool
    #: key → label of the type-specific fields.
    details: dict[str, str]


class LegalEntityFileCreate(EmployeeFileCreate):
    """Same fields and trimming as an employee's other file."""


class LegalEntityFileUpdate(EmployeeFileUpdate):
    """Same fields and trimming as an employee's other file."""


class LegalEntityFileResponse(_ORM):
    id: UUID
    legal_entity_id: UUID
    title: str
    category: str | None
    notes: str | None
    original_filename: str | None
    has_file: bool
    file_content_type: str | None
    created_at: datetime
    updated_at: datetime


class LegalEntityPaperwork(BaseModel):
    """Everything on file for one entity besides its licence and VAT certificate."""

    #: Every cycle, current first within each type.
    documents: list[LegalEntityDocumentResponse] = []
    #: Other files, newest first.
    files: list[LegalEntityFileResponse] = []


class LegalEntityDocumentSummary(_ORM):
    id: UUID
    doc_type: str
    document_number: str | None
    expiry_date: date | None


class LegalEntityPaperworkOverview(BaseModel):
    """The current cycle per type for one entity — the list's chips."""

    legal_entity_id: UUID
    current_documents: list[LegalEntityDocumentSummary] = []
