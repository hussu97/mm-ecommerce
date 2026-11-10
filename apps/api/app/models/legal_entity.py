from __future__ import annotations

import uuid
from datetime import date

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
from sqlalchemy.dialects.postgresql import ARRAY, UUID
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
