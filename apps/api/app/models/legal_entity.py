from __future__ import annotations

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class LegalEntity(Base, UUIDMixin, TimestampMixin):
    """A trade licence, optionally VAT-registered, that orders are booked under.

    Melting Moments trades under two: **Fatema Cake Sweets** (brand "Melting
    Moments Cakes"), VAT-registered, and **Najm AlShamal Coffee Shop LLC** (brand
    "Attibassi Coffee") at the Barsha counter, which is under the VAT threshold
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
    trade_license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
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

    def __repr__(self) -> str:
        return f"<LegalEntity {self.reference} {self.legal_name}>"
