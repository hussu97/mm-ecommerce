from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin
from .inventory import TRADE_LICENSE_AUTHORITIES

#: The seven emirates, spelled as the licences and the FTA spell them. The DB
#: CHECKs in migration `324_legal_entity_documents` list the same names.
EMIRATES: tuple[str, ...] = (
    "Abu Dhabi",
    "Dubai",
    "Sharjah",
    "Ajman",
    "Umm Al Quwain",
    "Ras Al Khaimah",
    "Fujairah",
)


#: The kind of activity a UAE trade licence permits, as the licensing
#: authorities classify them. Stored as the code; the label is for display.
#: Migration `324_legal_entity_documents` spells out the same codes in its CHECK.
TRADE_LICENSE_TYPES: dict[str, str] = {
    "commercial": "Commercial",
    "professional": "Professional",
    "industrial": "Industrial",
    "tourism": "Tourism",
    "crafts": "Crafts",
    "agricultural": "Agricultural",
    "e_trader": "e-Trader",
    "freelance": "Freelance",
    "other": "Other",
}


def _in_list(
    column: str, values: tuple[str, ...] | list[str], name: str
) -> CheckConstraint:
    allowed = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({allowed})", name=name)


class LegalEntity(Base, UUIDMixin, TimestampMixin):
    """A trade licence, optionally VAT-registered, that orders are booked under.

    Melting Moments trades under two: **Fatema Cake Sweets** (brand "Melting
    Moments Cakes"), VAT-registered, and **Najm AlShamal Coffee Shop LLC** (brand
    "Melting Moments Cafe") at the Barsha counter, which is under the VAT threshold
    and not registered. Each `(branch, channel)` points at one
    (`branch_channel_tax_configs.legal_entity_id`); the resolver freezes the
    chosen entity onto every order (`orders.legal_entity_id`), and the receipt,
    reports and admin all read identity, VAT status and logo from here.

    The receipt shows `brand_name` + `tax_number` (UAE lets a registrant invoice
    under a registered trade name); `legal_name` is for records and the VAT
    return. `logo_url` is a public `mm-product-images` object, fetched and printed
    by the till.
    """

    __tablename__ = "legal_entities"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_legal_entities_reference"),
        _in_list("vat_emirate", EMIRATES, "ck_legal_entities_vat_emirate"),
        CheckConstraint(
            "vat_tin IS NULL OR vat_tin ~ '^[0-9]{10}$'",
            name="ck_legal_entities_vat_tin_format",
        ),
    )

    #: Stable slug (`fatema`, `najm`) so migrations and code resolve an entity
    #: without hard-coding a UUID.
    reference: Mapped[str] = mapped_column(String(50), nullable=False)
    #: The trade-licence holder's legal name — records / VAT return, not printed
    #: on the receipt header.
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The registered trade name shown to the customer on the receipt.
    brand_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: When false, this entity charges no VAT — orders under it stamp zero VAT and
    #: write no `order_taxes` rows, and its receipt is titled a plain "Invoice".
    vat_registered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: TRN — present only when registered.
    tax_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    invoice_title: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default="Tax Invoice"
    )
    #: Mirror of the **current** trade-licence cycle (the one that expires
    #: last) — written only by ``legal_entity_documents.sync_current_license``,
    #: never edited directly. The full history is ``legal_entity_trade_licenses``.
    trade_license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trade_license_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Public GCS URL of the logo printed on this entity's receipts.
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # ─── What an A4 invoice needs that a till receipt does not (migration 295) ─
    #: The registered address printed under the legal name on a tax invoice.
    registered_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Printed on an invoice whose customer pays by bank transfer, and only then.
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    swift_code: Mapped[str | None] = mapped_column(String(11), nullable=True)
    #: Copied on every invoice emailed under this entity (the owners).
    invoice_cc_emails: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(255)), nullable=True
    )

    # ─── VAT registration + UAE e-invoicing (migration 324) ─────────────────
    #: The 10-digit Tax Identification Number UAE e-invoicing keys on — the
    #: first ten digits of the 15-digit TRN, shown on the EmaraTax profile.
    vat_tin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: The Peppol participant ID e-invoices are addressed to: ``0235:<TIN>``
    #: (0235 is the UAE scheme). Activated by the accredited service provider
    #: the books are connected to (Zoho Books' e-invoicing settings).
    peppol_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: Who issued the VAT certificate — the Federal Tax Authority in the UAE.
    vat_issuing_authority: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    #: The emirate of the VAT registration's registered address.
    vat_emirate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: "Effective Registration Date" on the certificate.
    vat_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The VAT registration certificate, in the private finance bucket
    #: (``GCS_INVOICE_BUCKET``), signed on read and never public.
    vat_certificate_object_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    vat_certificate_content_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    @property
    def has_vat_certificate(self) -> bool:
        return bool(self.vat_certificate_object_key)

    def __repr__(self) -> str:
        return f"<LegalEntity {self.reference} {self.legal_name}>"


class LegalEntityTradeLicense(Base, UUIDMixin, TimestampMixin):
    """One issue→expiry cycle of an entity's trade licence.

    A licence is renewed every year or few under the same number, and each
    renewal is a new document; every cycle is kept, so the history (and the
    scan that was valid at any date) is always on file. The cycle that expires
    last is the current one, mirrored onto ``legal_entities.trade_license_*``.

    ``issue_date`` is the date printed as the Issue Date — the licence's first
    issue on most UAE licences, not the renewal date.
    """

    __tablename__ = "legal_entity_trade_licenses"
    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id", "expiry_date", name="uq_legal_entity_trade_license_cycle"
        ),
        _in_list(
            "issuing_authority",
            list(TRADE_LICENSE_AUTHORITIES),
            "ck_legal_entity_trade_licenses_authority",
        ),
        _in_list("emirate", EMIRATES, "ck_legal_entity_trade_licenses_emirate"),
        _in_list(
            "license_type",
            list(TRADE_LICENSE_TYPES),
            "ck_legal_entity_trade_licenses_type",
        ),
        CheckConstraint(
            "issue_date IS NULL OR issue_date <= expiry_date",
            name="ck_legal_entity_trade_licenses_dates",
        ),
        Index("ix_legal_entity_trade_licenses_entity", "legal_entity_id"),
    )

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    license_number: Mapped[str] = mapped_column(String(100), nullable=False)
    #: The commercial register number printed beside the licence number.
    registration_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: A key of ``TRADE_LICENSE_AUTHORITIES`` (the same list suppliers use).
    issuing_authority: Mapped[str] = mapped_column(String(40), nullable=False)
    #: A key of ``TRADE_LICENSE_TYPES`` — commercial, professional, industrial…
    license_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: The legal form printed on the licence ("Limited Liability Company -
    #: Single Owner (LLC - SO)", "Services Agency", "Sole Establishment"…).
    legal_form: Mapped[str | None] = mapped_column(String(120), nullable=True)
    emirate: Mapped[str] = mapped_column(String(20), nullable=False)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: This cycle's licence document, private bucket, signed on read.
    document_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_content_type: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    @property
    def has_document(self) -> bool:
        return bool(self.document_object_key)

    def __repr__(self) -> str:
        return f"<LegalEntityTradeLicense {self.license_number} → {self.expiry_date}>"


# ─── Other registration documents (migration 330) ─────────────────────────────

#: The document kinds an entity keeps besides its trade licence and VAT
#: certificate, in the order the console shows them. Mirrored by the CHECKs in
#: migration `330_legal_entity_documents`.
#:
#: One current cycle per type per entity: each entity manages exactly one shop
#: (Najm → Barsha, Fatema → the Sharjah kitchen; the other branches' premises
#: are not the shop's to keep papers for), so the ``Premises`` group — tenancy,
#: food permit, Civil Defence… — is that one shop's.
#: ``expires`` — false for a document issued once (the Corporate Tax
#: certificate, the MoA): no expiry date, the latest issued is current.
#: ``expiry_tracked`` — feeds the super-admin's expiring-paperwork warning.
#: ``authorities`` — suggestions for the issuer box, per emirate where it differs.
#: ``details`` — the type-specific fields kept in ``details`` (key → label).
LEGAL_ENTITY_DOCUMENT_SPECS: dict[str, dict] = {
    "establishment_card": {
        "label": "Establishment card",
        "group": "Visas & labour",
        "hint": "The immigration card that lets the company sponsor visas. Renewed yearly; a lapsed card blocks every visa service.",
        "number_label": "Establishment number",
        "authority_label": "Issued by",
        "authorities": ["ICP (Sharjah)", "GDRFA Dubai"],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "name": "Establishment name as printed",
            "license_number": "Trade licence number",
            "sector": "Sector / legal form",
            "signatory": "Authorised signatory",
        },
    },
    "mohre_card": {
        "label": "Labour company card",
        "group": "Visas & labour",
        "hint": "MOHRE's establishment card (the company's labour file). A lapsed card blocks work permits and WPS.",
        "number_label": "MOHRE establishment number",
        "authority_label": "Issued by",
        "authorities": ["Ministry of Human Resources & Emiratisation (MOHRE)"],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "name": "Establishment name as printed",
            "license_number": "Trade licence number",
            "classification": "MOHRE classification / category",
            "signatory": "Authorised signatory",
        },
    },
    "chamber_certificate": {
        "label": "Chamber of Commerce membership",
        "group": "Registration",
        "hint": "Membership certificate, renewed with the trade licence. Needed for the licence renewal and certificates of origin.",
        "number_label": "Membership number",
        "authority_label": "Chamber",
        "authorities": [
            "Sharjah Chamber of Commerce & Industry",
            "Dubai Chambers",
        ],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "license_number": "Trade licence number",
            "grade": "Membership grade / category",
            "legal_form": "Legal form",
        },
    },
    "corporate_tax": {
        "label": "Corporate Tax registration",
        "group": "Registration",
        "hint": "The FTA's Corporate Tax certificate. Issued once — every entity must hold one, VAT-registered or not.",
        "number_label": "Corporate Tax TRN",
        "authority_label": "Issued by",
        "authorities": ["Federal Tax Authority (FTA)"],
        "expires": False,
        "expiry_tracked": False,
        "details": {
            "effective_date": "Effective registration date",
            "tax_period": "Tax period",
            "first_return_due": "First return due",
        },
    },
    "moa": {
        "label": "Memorandum of Association",
        "group": "Registration",
        "hint": "The founding contract. No expiry — add the amended one when owners or shares change.",
        "number_label": "Reference / notarisation number",
        "authority_label": "Notarised by",
        "authorities": [
            "Notary Public — Sharjah Courts",
            "Notary Public — Dubai Courts",
        ],
        "expires": False,
        "expiry_tracked": False,
        "details": {
            "owners": "Owners / partners",
            "capital": "Share capital",
            "manager": "Manager",
        },
    },
    "tenancy_contract": {
        "label": "Tenancy contract",
        "group": "Premises",
        "hint": "Ejari in Dubai, the municipality-attested lease in Sharjah. The licence and the MOHRE file cannot renew without a valid one.",
        "number_label": "Ejari / contract number",
        "authority_label": "Registered with",
        "authorities": [
            "Dubai Land Department (Ejari)",
            "Sharjah City Municipality",
        ],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "landlord": "Landlord",
            "premises": "Unit / premises",
            "plot_number": "Plot / Makani number",
            "annual_rent": "Annual rent",
        },
    },
    "food_permit": {
        "label": "Municipality food permit",
        "group": "Premises",
        "hint": "The municipality's food-establishment approval / health certificate for this shop. Checked on inspection and at licence renewal.",
        "number_label": "Permit / approval number",
        "authority_label": "Issued by",
        "authorities": [
            "Sharjah City Municipality — Food Control",
            "Dubai Municipality — Food Safety Department",
        ],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "activity": "Activity",
            "risk_category": "Risk category",
            "person_in_charge": "Person in charge (PIC)",
        },
    },
    "civil_defence": {
        "label": "Civil Defence certificate",
        "group": "Premises",
        "hint": "Fire-safety compliance for this shop, usually annual with the fire-system maintenance contract.",
        "number_label": "Certificate number",
        "authority_label": "Issued by",
        "authorities": [
            "Sharjah Civil Defence Authority",
            "Dubai Civil Defence",
        ],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "risk_class": "Risk class",
            "maintenance_contractor": "Fire-safety maintenance contractor",
        },
    },
    "signage_permit": {
        "label": "Signage permit",
        "group": "Premises",
        "hint": "Permit for the shopfront sign / trade-name board.",
        "number_label": "Permit number",
        "authority_label": "Issued by",
        "authorities": [
            "Sharjah City Municipality",
            "Department of Economy and Tourism (Dubai)",
        ],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "sign_text": "Trade name on the sign",
            "size": "Sign size",
        },
    },
    "pest_control": {
        "label": "Pest-control contract",
        "group": "Premises",
        "hint": "The approved pest-control contractor's annual contract — inspectors ask for it.",
        "number_label": "Contract number",
        "authority_label": "Contractor",
        "authorities": [],
        "expires": True,
        "expiry_tracked": True,
        "details": {
            "visit_frequency": "Visit frequency",
            "contact": "Contractor contact",
        },
    },
}

LEGAL_ENTITY_DOCUMENT_TYPES: tuple[str, ...] = tuple(LEGAL_ENTITY_DOCUMENT_SPECS)
#: Issued once, no expiry date.
NON_EXPIRING_ENTITY_DOCUMENT_TYPES: tuple[str, ...] = tuple(
    t for t, s in LEGAL_ENTITY_DOCUMENT_SPECS.items() if not s["expires"]
)
ENTITY_EXPIRY_TRACKED_TYPES: tuple[str, ...] = tuple(
    t for t, s in LEGAL_ENTITY_DOCUMENT_SPECS.items() if s["expiry_tracked"]
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class LegalEntityDocument(Base, UUIDMixin, TimestampMixin):
    """One issue→expiry cycle of one of an entity's registration documents.

    Versioned like an employee's: a renewal adds a row, the old one stays on
    file with its scan, and the cycle expiring last per type is the current
    one. A non-expiring type has no expiry; its latest issued is current.
    """

    __tablename__ = "legal_entity_documents"
    __table_args__ = (
        CheckConstraint(
            f"doc_type IN ({_quoted(LEGAL_ENTITY_DOCUMENT_TYPES)})",
            name="ck_legal_entity_documents_doc_type",
        ),
        CheckConstraint(
            "expiry_date IS NOT NULL OR doc_type IN "
            f"({_quoted(NON_EXPIRING_ENTITY_DOCUMENT_TYPES)})",
            name="ck_legal_entity_documents_expiry",
        ),
        UniqueConstraint(
            "legal_entity_id",
            "doc_type",
            "expiry_date",
            name="uq_legal_entity_documents_cycle",
        ),
        CheckConstraint(
            "issue_date IS NULL OR expiry_date IS NULL OR issue_date <= expiry_date",
            name="ck_legal_entity_documents_dates",
        ),
        Index("ix_legal_entity_documents_expiry", "expiry_date"),
    )

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[str] = mapped_column(String(30), nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: The type-specific fields named in `LEGAL_ENTITY_DOCUMENT_SPECS`.
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @property
    def has_file(self) -> bool:
        return bool(self.file_object_key)

    def __repr__(self) -> str:
        return f"<LegalEntityDocument {self.doc_type} → {self.expiry_date}>"


class LegalEntityFile(Base, UUIDMixin, TimestampMixin):
    """Any other paper kept against an entity — a power of attorney, a UBO
    filing, an insurance policy — with a free-text type and no expiry, like an
    `EmployeeFile`."""

    __tablename__ = "legal_entity_files"
    __table_args__ = (Index("ix_legal_entity_files_entity", "legal_entity_id"),)

    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @property
    def has_file(self) -> bool:
        return bool(self.file_object_key)

    def __repr__(self) -> str:
        return f"<LegalEntityFile {self.title}>"
