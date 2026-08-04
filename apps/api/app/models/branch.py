from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from .device import Device
    from .pos_table import Section
    from .tax import TaxGroup


class BranchTypeEnum(str, enum.Enum):
    """What a branch physically is. Mirrors Foodics `branch.type`."""

    RESTAURANT = "restaurant"
    KITCHEN = "kitchen"
    WAREHOUSE = "warehouse"


class Branch(Base, UUIDMixin, TimestampMixin):
    """
    A physical location — shop, production kitchen or warehouse.

    Almost every operational record in the POS domain (orders, tills, stock levels,
    devices, staff assignments) hangs off a branch.
    """

    __tablename__ = "branches"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(200), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=BranchTypeEnum.RESTAURANT.value
    )

    # Location & contact
    latitude: Mapped[Any | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Any | None] = mapped_column(Numeric(10, 7), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Trading hours. `opening_from`/`opening_to` are "HH:MM" strings so a branch can
    # trade past midnight (e.g. 09:00 → 02:00) without date arithmetic.
    opening_from: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="00:00"
    )
    opening_to: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="23:59"
    )
    # Cut-off that rolls the trading day over — orders before it belong to the
    # previous business_date. Also used as the inventory end-of-day.
    business_day_start: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="04:00"
    )
    inventory_end_of_day_time: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="04:00"
    )

    # Receipt overrides — fall back to BusinessSettings when null.
    receipt_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_footer: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Tax identity (printed on the invoice, required for UAE FTA compliance)
    tax_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tax_registration_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    tax_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Courier identity. A branch is a pickup location as far as a courier is
    # concerned, so which outlet a courier collects from belongs here rather
    # than in the environment — the same place its coordinates and phone number
    # already live. Lalamove needs nothing but those two; noon Send additionally
    # requires the branch to be registered with them, and calls the result an
    # outlet code.
    #
    # Null means this branch cannot dispatch through noon Send. Register it with
    # `python -m scripts.register_noon_send_pickup --create --branch <ref>`,
    # which writes the code it gets back into this column.
    noon_send_outlet_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: The outlet's exact address revision — `addr::restaurant_outlet::ae::X::2`.
    #: Optional on noon's side and optional here: the outlet code alone
    #: identifies where a rider collects, and this is a nicety that pins the
    #: address revision with it. Longer than the code because it carries a
    #: registry, a country and a revision as well.
    noon_send_outlet_address_code: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )

    # Capabilities
    receives_online_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    accepts_reservations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    reservation_duration: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60"
    )
    reservation_times: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    tax_group: Mapped[TaxGroup | None] = relationship("TaxGroup")
    devices: Mapped[list[Device]] = relationship(
        "Device", back_populates="branch", cascade="all, delete-orphan"
    )
    sections: Mapped[list[Section]] = relationship(
        "Section", back_populates="branch", cascade="all, delete-orphan"
    )
    business_days: Mapped[list[BranchBusinessDay]] = relationship(
        "BranchBusinessDay", back_populates="branch", cascade="all, delete-orphan"
    )

    def name_for(self, locale: str = "en") -> str:
        if isinstance(self.translations, dict):
            value = self.translations.get(locale)
            if value:
                return str(value)
        return self.name

    def __repr__(self) -> str:
        return f"<Branch {self.reference} {self.name}>"


class BranchBusinessDay(Base, UUIDMixin, TimestampMixin):
    """
    One trading day per branch. Opened when the first till of the day opens and
    closed by "end of day"; every order, till and drawer operation is stamped with
    its `business_date` so reporting is not distorted by post-midnight trading.
    """

    __tablename__ = "branch_business_days"
    __table_args__ = (
        UniqueConstraint("branch_id", "business_date", name="uq_branch_business_date"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Z-report totals, frozen at end of day.
    total_sales: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_orders: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_discounts: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_returns: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    total_taxes: Mapped[Any] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )

    branch: Mapped[Branch] = relationship("Branch", back_populates="business_days")

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def __repr__(self) -> str:
        return f"<BranchBusinessDay {self.branch_id} {self.business_date}>"


DEFAULT_OPENING_AMOUNT = Decimal("0.00")
