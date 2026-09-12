from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .legal_entity import LegalEntity
    from .tax import TaxGroup


class ChannelClassEnum(str, enum.Enum):
    """The three sales-channel classes a tax identity can differ across.

    `Order.source` maps onto these — cashier → counter, online → website,
    aggregator → aggregator — in `tax_identity_service.channel_class_for`, the
    one place that mapping is decided.
    """

    COUNTER = "counter"
    WEBSITE = "website"
    AGGREGATOR = "aggregator"


class BranchChannelTaxConfig(Base, UUIDMixin, TimestampMixin):
    """VAT treatment and trade-license identity for one (branch, channel).

    A branch can trade under different licenses per channel: Barsha's counter is
    not VAT-registered while its website/aggregator sales are, under the Melting
    Moments license. The single per-branch `tax_number`/`tax_registration_name`/
    `tax_group_id` cannot express that, and the same receipt renderer prints
    every channel — so the identity is resolved per (branch, channel) here and
    frozen onto each order at creation (`orders.tax_number` et al).

    A missing/inactive row means "behave as today": VAT-registered, identity
    inherited from the branch and `business_settings`. Every field except
    `vat_registered` inherits per-field when null, so a row can flip only the
    registration and keep the inherited identity. `tax_group_id` is a per-channel
    VAT-group override; null leaves the existing per-product/per-branch tax-group
    resolution unchanged.
    """

    __tablename__ = "branch_channel_tax_configs"
    __table_args__ = (
        UniqueConstraint("branch_id", "channel_class", name="uq_branch_channel_tax"),
        CheckConstraint(
            "channel_class IN ('counter','website','aggregator')",
            name="ck_branch_channel_tax_class",
        ),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel_class: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The legal entity (trade licence) this channel trades under. Its
    #: `vat_registered` decides whether VAT is charged; its brand/TRN/title/logo
    #: are what the receipt shows and what the reports group by.
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("legal_entities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Optional per-channel VAT-group override. Null keeps the product/branch
    #: tax-group resolution the pricing engine already does.
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    legal_entity: Mapped[LegalEntity] = relationship("LegalEntity")
    tax_group: Mapped[TaxGroup | None] = relationship("TaxGroup")

    def __repr__(self) -> str:
        return (
            f"<BranchChannelTaxConfig {self.branch_id} {self.channel_class} "
            f"entity={self.legal_entity_id}>"
        )
