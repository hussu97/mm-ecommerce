from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    TimestampMixin,
    UUIDMixin,
    business_date_format,
    status_vocabulary,
)


class VatDirectionEnum(str, enum.Enum):
    """Which side of the VAT return a row lands on.

    ``output`` is VAT the shop collected on sales and owes the FTA; ``input`` is
    VAT the shop paid on its costs and may reclaim when the entity is registered.
    """

    OUTPUT = "output"
    INPUT = "input"


class VatCategoryEnum(str, enum.Enum):
    """The bucket a VAT amount belongs to, so the report reads by category.

    Output categories come from orders; input categories from the fees the shop
    is billed and from purchases. Adding a category needs a widening migration
    (the CHECK is spelled out) — see ``260_vat_ledger``.
    """

    #: Output VAT on completed sales.
    SALES_OUTPUT = "sales_output"
    #: Credit notes / refunds, stored negative so output VAT is net of them.
    SALES_REFUND = "sales_refund"
    #: Input VAT on marketplace commission.
    AGGREGATOR_COMMISSION = "aggregator_commission"
    #: Input VAT on card-processor / marketplace payment fees.
    PAYMENT_PROCESSING = "payment_processing"
    #: Input VAT on what the courier charged the shop.
    COURIER_FEES = "courier_fees"
    #: Input VAT on purchased raw goods (recoverable slice of received POs).
    RAW_GOODS = "raw_goods"
    #: Input VAT on merchant-funded marketplace marketing.
    MARKETPLACE_MARKETING = "marketplace_marketing"
    #: Input VAT on marketplace cancellation fees.
    MARKETPLACE_CANCELLATION = "marketplace_cancellation"
    #: Input VAT on marketplace charges no order carries, dated by statement:
    #: noon's monthly platform and long-distance fees, Deliveroo's monthly admin
    #: fee and its correction credits (a credit is stored negative). See
    #: `services/aggregators/period_charges`. Widened in ``288_vat_period_charges``.
    MARKETPLACE_PERIOD_CHARGES = "marketplace_period_charges"


class VatLedgerEntry(Base, UUIDMixin, TimestampMixin):
    """A derived, per-day VAT figure for one legal entity and category.

    **A cache, not a source of truth.** Every row is recomputed idempotently by
    ``app.services.vat_ledger`` from the underlying tables (orders, their fee
    columns, order deliveries, purchase orders, and the marketplace statement
    lines that belong to no order) — the sweep deletes a window's
    rows and rebuilds them, so nothing here is ever hand-edited. It exists so the
    console can read a legal entity's VAT position in one cheap query instead of
    re-aggregating five source tables on every page load.

    One row per ``(business_date, legal_entity_id, category, direction)``. Each
    carries an explicit net / VAT / gross split. For an entity that is not
    VAT-registered (the Barsha counter's Najm AlShamal), input rows are written
    with ``vat_amount = 0`` and ``vat_recoverable = false`` — the cost is visible
    but not reclaimable — and output rows already carry zero VAT from the order.
    """

    __tablename__ = "vat_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "business_date",
            "legal_entity_id",
            "category",
            "direction",
            name="uq_vat_ledger_grain",
        ),
        status_vocabulary("vat_ledger_entries", "direction", VatDirectionEnum),
        status_vocabulary("vat_ledger_entries", "category", VatCategoryEnum),
        business_date_format("vat_ledger_entries"),
        Index("ix_vat_ledger_entity_date", "legal_entity_id", "business_date"),
        Index("ix_vat_ledger_business_date", "business_date"),
    )

    #: The frozen trading day (YYYY-MM-DD), same basis as ``orders.business_date``.
    business_date: Mapped[str] = mapped_column(String(10), nullable=False)
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    #: Value excluding VAT.
    net_value: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    #: VAT collected (output) or recoverable (input); zero for a non-registered
    #: entity's input rows.
    vat_amount: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    #: ``net_value + vat_amount`` (or the raw inclusive figure), stored for display
    #: and reconciliation.
    gross_value: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    #: False on an input row whose entity is not VAT-registered — the cost shows
    #: but the VAT cannot be reclaimed. Always true for output rows.
    vat_recoverable: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    #: How many source rows fed this grain, so "0 because none" is distinct from
    #: "no data yet".
    source_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    #: When the sweep last rebuilt this row.
    recomputed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<VatLedgerEntry {self.business_date} {self.legal_entity_id} "
            f"{self.category} {self.direction} vat={self.vat_amount}>"
        )
