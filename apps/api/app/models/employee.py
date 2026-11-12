"""Employees and their right-to-work paperwork (super-admin only).

An *employee* is a person the shop employs, whatever they do — not a console or
till account (that is a `User`). Most employees never sign in to anything; the
record exists because UAE employment hangs on documents that each expire: the
passport, the residence visa, the Emirates ID, the MOHRE labour card (work
permit), the municipality Occupational Health Card, the medical insurance the
employer must provide, and the WPS salary card the wages are paid onto. A
lapsed visa, Emirates ID, labour card, OHC or policy is a fine for the
employer, and a lapsed salary card is a payroll that cannot be paid out, so
the console warns a super-admin 15 days ahead.

Each document is versioned by its issue→expiry cycle: a renewal adds a row and
the old one stays on file with its scan. The row expiring last is the current
one. The scans live in the private finance bucket, signed on read.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .user import User

#: Employment status. Mirrored by the CHECK in migration `325_employees`.
EMPLOYEE_STATUSES: tuple[str, ...] = ("active", "left")

#: The document types, in the order the console shows them. Mirrored by the
#: CHECK in migration `328_employee_insurance_wps` (the latest to widen
#: `325_employees`'s).
EMPLOYEE_DOCUMENT_TYPES: tuple[str, ...] = (
    "passport",
    "visa",
    "emirates_id",
    "labour_card",
    "ohc",
    "medical_insurance",
    "wps_card",
)

#: The types whose lapse the expiry warning tracks. A passport is renewed long
#: before it matters (the visa cannot outlive it), so the owner left it out.
#: The Emirates ID is tracked: the card is renewed with the visa but is its own
#: expiry, and a lapsed one is a fine of its own. So is the health policy (the
#: employer must insure every employee) and the salary card (WPS pays onto it).
EXPIRY_TRACKED_TYPES: tuple[str, ...] = (
    "visa",
    "emirates_id",
    "labour_card",
    "ohc",
    "medical_insurance",
    "wps_card",
)

#: Per type: the label, what its number is called, what `issuing_authority` is
#: called when it is not "Issuing authority", and the type-specific fields kept
#: in `employee_documents.details` (key → label). Served to the console by
#: `GET /employees/document-types`, so the form and this list cannot drift.
EMPLOYEE_DOCUMENT_SPECS: dict[str, dict[str, Any]] = {
    "passport": {
        "label": "Passport",
        "number_label": "Passport number",
        "details": {
            "surname": "Surname",
            "given_names": "Given names",
            #: Printed by some passports (the Philippines') and absent from the MRZ.
            "middle_name": "Middle name",
            "nationality": "Nationality",
            "date_of_birth": "Date of birth",
            "sex": "Sex",
            "place_of_birth": "Place of birth",
            "place_of_issue": "Place of issue",
        },
    },
    "visa": {
        "label": "Residence visa",
        "number_label": "File number",
        "details": {
            "uid_number": "UID number",
            "passport_number": "Passport number",
            "profession": "Profession",
            "sponsor": "Sponsor / employer",
            "place_of_issue": "Place of issue",
        },
    },
    "emirates_id": {
        "label": "Emirates ID",
        #: The 15-digit identity number (784-YYYY-NNNNNNN-C). It is the person's
        #: for life and survives a renewal; the card number below does not.
        "number_label": "ID number",
        "details": {
            "card_number": "Card number",
            "name": "Name as printed",
            "nationality": "Nationality",
            "date_of_birth": "Date of birth",
            "sex": "Sex",
            "occupation": "Occupation",
            "employer": "Employer",
            "place_of_issue": "Issuing place",
        },
    },
    "labour_card": {
        "label": "Labour card",
        "number_label": "Work permit number",
        "details": {
            "personal_number": "Personal number",
            "profession": "Profession",
            "establishment": "Establishment",
        },
    },
    "ohc": {
        "label": "Occupational Health Card",
        "number_label": "Card number",
        "details": {
            "reference": "Reference / audit trail",
            "result": "Medical result / remarks",
        },
    },
    "medical_insurance": {
        "label": "Medical insurance",
        #: What a clinic asks for: the member / card number on the insurance card.
        "number_label": "Member / card number",
        "authority_label": "Insurer",
        "details": {
            "policy_number": "Policy number",
            "name": "Name as printed",
            #: The third-party administrator that runs the claims (NAS, NextCare…).
            "tpa": "TPA / administrator",
            "network": "Network / plan",
            "category": "Category / class",
        },
    },
    "wps_card": {
        "label": "WPS salary card",
        #: A payroll card number is a payment-card number. Only its last four
        #: digits are kept — `employee_service` refuses a full one — and the
        #: scan in the private bucket is the full record.
        "number_label": "Card number (last 4 digits)",
        "authority_label": "Bank / exchange house",
        "details": {
            "name": "Name as printed",
            "employee_code": "Employee / personal code",
            "account_number": "Account / IBAN",
            "routing_code": "Agent routing code",
        },
    },
}


def _in_list(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class Employee(Base, UUIDMixin, TimestampMixin):
    """A person on the payroll, with the identity the documents carry."""

    __tablename__ = "employees"
    __table_args__ = (
        _in_list("status", EMPLOYEE_STATUSES, "ck_employees_status"),
        CheckConstraint(
            "gender IS NULL OR gender IN ('M', 'F')", name="ck_employees_gender"
        ),
        Index("ix_employees_status", "status"),
        Index("ix_employees_legal_entity", "legal_entity_id"),
        Index("ix_employees_branch", "branch_id"),
        UniqueConstraint("user_id", name="uq_employees_user"),
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name_ar: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(80), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The MOHRE person code — printed on every labour card as "Personal No"
    #: and stable across renewals, unlike the work-permit number.
    personal_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: The trade licence that sponsors the visa ("Establishment" on the card).
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The person's console / till login, when they have one — one employee
    #: per account. Nothing in auth reads it; it ties the HR record to the
    #: account that rings up their sales.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    joined_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    left_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: `left` keeps the record and its history but drops it from expiry warnings.
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="active"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Passport-style photo, private bucket, signed on read.
    photo_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    user: Mapped[User | None] = relationship("User", lazy="selectin")
    documents: Mapped[list[EmployeeDocument]] = relationship(
        "EmployeeDocument",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="EmployeeDocument.expiry_date.desc()",
        lazy="selectin",
    )

    @property
    def has_photo(self) -> bool:
        return bool(self.photo_object_key)

    @property
    def user_email(self) -> str | None:
        return self.user.email if self.user else None

    @property
    def user_display_name(self) -> str | None:
        return (self.user.display_name or self.user.email) if self.user else None

    def __repr__(self) -> str:
        return f"<Employee {self.full_name}>"


class EmployeeDocument(Base, UUIDMixin, TimestampMixin):
    """One issue→expiry cycle of one of an employee's documents."""

    __tablename__ = "employee_documents"
    __table_args__ = (
        _in_list("doc_type", EMPLOYEE_DOCUMENT_TYPES, "ck_employee_documents_doc_type"),
        UniqueConstraint(
            "employee_id",
            "doc_type",
            "expiry_date",
            name="uq_employee_documents_cycle",
        ),
        CheckConstraint(
            "issue_date IS NULL OR issue_date <= expiry_date",
            name="ck_employee_documents_dates",
        ),
        Index("ix_employee_documents_employee", "employee_id", "doc_type"),
        Index("ix_employee_documents_expiry", "expiry_date"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Passport no., visa file no., work-permit no., OHC card no., Emirates ID
    #: no., insurance member no.; a WPS card's last four digits only.
    document_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The type-specific fields named in `EMPLOYEE_DOCUMENT_SPECS`.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    employee: Mapped[Employee] = relationship("Employee", back_populates="documents")

    @property
    def has_file(self) -> bool:
        return bool(self.file_object_key)

    def __repr__(self) -> str:
        return f"<EmployeeDocument {self.doc_type} → {self.expiry_date}>"
