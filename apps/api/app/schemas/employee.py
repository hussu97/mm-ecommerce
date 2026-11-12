"""Employees, their versioned documents, their other files, and the
expiring-paperwork warning."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.employee import (
    EMPLOYEE_DOCUMENT_SPECS,
    EMPLOYEE_DOCUMENT_TYPES,
    EMPLOYEE_STATUSES,
)

EmployeeStatusLiteral = Literal[EMPLOYEE_STATUSES]  # type: ignore[valid-type]
EmployeeDocTypeLiteral = Literal[EMPLOYEE_DOCUMENT_TYPES]  # type: ignore[valid-type]
GenderLiteral = Literal["M", "F"]


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─── Employees ────────────────────────────────────────────────────────────────


class _EmployeeFields(BaseModel):
    full_name_ar: str | None = Field(None, max_length=200)
    nationality: str | None = Field(None, max_length=80)
    date_of_birth: date | None = None
    gender: GenderLiteral | None = None
    job_title: str | None = Field(None, max_length=120)
    personal_number: str | None = Field(None, max_length=30)
    phone: str | None = Field(None, max_length=30)
    legal_entity_id: UUID | None = None
    branch_id: UUID | None = None
    #: The person's console / till account (a staff user), if any.
    user_id: UUID | None = None
    joined_on: date | None = None
    left_on: date | None = None
    notes: str | None = Field(None, max_length=4000)


class EmployeeCreate(_EmployeeFields):
    full_name: str = Field(min_length=1, max_length=200)
    status: EmployeeStatusLiteral = "active"  # type: ignore[valid-type]


class EmployeeUpdate(_EmployeeFields):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    status: EmployeeStatusLiteral | None = None  # type: ignore[valid-type]


class EmployeeDocumentSummary(_ORM):
    """The current cycle of one document type, for the list's columns."""

    id: UUID
    doc_type: str
    document_number: str | None
    expiry_date: date


class EmployeeListItem(_ORM):
    id: UUID
    full_name: str
    nationality: str | None
    job_title: str | None
    status: str
    legal_entity_id: UUID | None
    branch_id: UUID | None
    user_id: UUID | None = None
    user_display_name: str | None = None
    has_photo: bool
    #: The current cycle per type; a type with no document is absent.
    current_documents: dict[str, EmployeeDocumentSummary] = {}


class EmployeeDocumentResponse(_ORM):
    id: UUID
    employee_id: UUID
    doc_type: str
    document_number: str | None
    issue_date: date | None
    expiry_date: date
    issuing_authority: str | None
    details: dict[str, str]
    notes: str | None
    has_file: bool
    file_content_type: str | None
    #: The cycle expiring last for its type.
    is_current: bool = False
    created_at: datetime
    updated_at: datetime


class EmployeeFileResponse(_ORM):
    id: UUID
    employee_id: UUID
    title: str
    category: str | None
    notes: str | None
    original_filename: str | None
    has_file: bool
    file_content_type: str | None
    created_at: datetime
    updated_at: datetime


class EmployeeResponse(_ORM):
    id: UUID
    full_name: str
    full_name_ar: str | None
    nationality: str | None
    date_of_birth: date | None
    gender: str | None
    job_title: str | None
    personal_number: str | None
    phone: str | None
    legal_entity_id: UUID | None
    branch_id: UUID | None
    user_id: UUID | None = None
    user_email: str | None = None
    user_display_name: str | None = None
    joined_on: date | None
    left_on: date | None
    status: str
    notes: str | None
    has_photo: bool
    documents: list[EmployeeDocumentResponse] = []
    #: Other files, newest first.
    files: list[EmployeeFileResponse] = []
    created_at: datetime
    updated_at: datetime


# ─── Documents ────────────────────────────────────────────────────────────────


class _DocumentDates(BaseModel):
    @model_validator(mode="after")
    def _issue_before_expiry(self):
        issued = getattr(self, "issue_date", None)
        expires = getattr(self, "expiry_date", None)
        if issued is not None and expires is not None and issued > expires:
            raise ValueError("The issue date must be on or before the expiry date")
        return self


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


class EmployeeDocumentCreate(_DocumentDates):
    doc_type: EmployeeDocTypeLiteral  # type: ignore[valid-type]
    document_number: str | None = Field(None, max_length=100)
    issue_date: date | None = None
    expiry_date: date
    issuing_authority: str | None = Field(None, max_length=120)
    details: dict[str, str | None] = {}
    notes: str | None = Field(None, max_length=2000)

    @field_validator("details")
    @classmethod
    def _clean(cls, value):
        return _clean_details(value)

    @model_validator(mode="after")
    def _known_details(self):
        allowed = EMPLOYEE_DOCUMENT_SPECS[self.doc_type]["details"]
        unknown = sorted(set(self.details) - set(allowed))
        if unknown:
            raise ValueError(f"Unknown {self.doc_type} field(s): {', '.join(unknown)}")
        return self


class EmployeeDocumentUpdate(_DocumentDates):
    """`doc_type` is fixed once created; the key check runs in the service."""

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


class EmployeeDocumentTypeSpec(BaseModel):
    doc_type: str
    label: str
    number_label: str
    #: What `issuing_authority` is called for this type ("Insurer", …).
    authority_label: str = "Issuing authority"
    #: Raises the expiry warning when it lapses.
    expiry_tracked: bool
    #: key → label of the type-specific fields.
    details: dict[str, str]


# ─── Other files ──────────────────────────────────────────────────────────────


def _blank_to_none(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


class EmployeeFileCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=4000)
    original_filename: str | None = Field(None, max_length=255)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A name is required")
        return value

    @field_validator("category", "notes", "original_filename")
    @classmethod
    def _blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class EmployeeFileUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=4000)
    original_filename: str | None = Field(None, max_length=255)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A name is required")
        return value

    @field_validator("category", "notes", "original_filename")
    @classmethod
    def _blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class SignedDocumentUrl(BaseModel):
    """A private file, signed twice: `url` shows it inline, `download_url`
    (Content-Disposition: attachment) saves it as `filename`."""

    url: str
    download_url: str
    filename: str
    content_type: str | None = None


# ─── Expiring paperwork ──────────────────────────────────────────────────────


class ExpiringDocument(BaseModel):
    """A current document that lapses within the warning window (or has)."""

    owner_kind: Literal["employee", "legal_entity"]
    owner_id: UUID
    owner_name: str
    #: An `EXPIRY_TRACKED_TYPES` value for an employee, `trade_license` for an entity.
    doc_type: str
    doc_label: str
    document_number: str | None
    expiry_date: date
    #: Negative once expired.
    days_left: int


class ExpiringDocumentsResponse(BaseModel):
    within_days: int
    today: date
    items: list[ExpiringDocument]
