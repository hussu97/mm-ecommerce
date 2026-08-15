from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class TaxTypeEnum(str, enum.Enum):
    """
    INCLUSIVE — the menu price already contains the tax (UAE VAT default).
    EXCLUSIVE — the tax is added on top of the menu price at checkout.
    """

    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


class Tax(Base, UUIDMixin, TimestampMixin):
    """A single tax rate, e.g. "UAE VAT 5%" or "Municipality Fee 7%"."""

    __tablename__ = "taxes"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Stored as a fraction (0.0500 == 5%) to match the existing Order.vat_rate.
    # `_rate` = fraction, `_percent` = percentage — the convention is spelled
    # out in full on `Order.vat_rate`, beside the column it is easiest to
    # confuse this one with (`payment_gateways.fee_percent`, which is 2.9).
    rate: Mapped[Any] = mapped_column(Numeric(6, 4), nullable=False)
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=TaxTypeEnum.INCLUSIVE.value
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tax_groups: Mapped[list[TaxGroupTax]] = relationship(
        "TaxGroupTax", back_populates="tax", cascade="all, delete-orphan"
    )

    @property
    def rate_percent(self) -> float:
        return float(self.rate) * 100

    def __repr__(self) -> str:
        return f"<Tax {self.name} {self.rate_percent:.2f}%>"


class TaxGroup(Base, UUIDMixin, TimestampMixin):
    """
    A named bundle of taxes applied together. Products, charges and branches point
    at a tax group rather than at individual taxes, so rates can be changed in one
    place (e.g. "UAE VAT" -> VAT 5%).
    """

    __tablename__ = "tax_groups"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    taxes: Mapped[list[TaxGroupTax]] = relationship(
        "TaxGroupTax",
        back_populates="tax_group",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def combined_rate(self) -> float:
        """Sum of the member tax rates, as a fraction."""
        return sum(float(link.tax.rate) for link in self.taxes if link.tax)

    def __repr__(self) -> str:
        return f"<TaxGroup {self.name}>"


class TaxGroupTax(Base, UUIDMixin):
    """Join row between a tax group and a tax."""

    __tablename__ = "tax_group_taxes"
    __table_args__ = (
        UniqueConstraint("tax_group_id", "tax_id", name="uq_tax_group_tax"),
    )

    tax_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tax_group: Mapped[TaxGroup] = relationship("TaxGroup", back_populates="taxes")
    tax: Mapped[Tax] = relationship("Tax", back_populates="tax_groups", lazy="selectin")

    def __repr__(self) -> str:
        return f"<TaxGroupTax group={self.tax_group_id} tax={self.tax_id}>"
