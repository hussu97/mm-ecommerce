from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class KitchenFlow(Base, UUIDMixin, TimestampMixin):
    """
    A kitchen station and the routing rule that feeds it — "Hot Line", "Barista",
    "Pastry". When an order is sent to the kitchen each line is matched against
    every active flow for the branch; matching lines appear on that flow's KDS
    screens and print to its kitchen printers.

    A line that matches no flow falls back to the branch's default flow so nothing
    is silently dropped.
    """

    __tablename__ = "kitchen_flows"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(120), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Empty order_types means "every order type".
    order_types: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Auto-bump a ticket this many seconds after it is marked ready (0 = manual).
    auto_complete_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    categories: Mapped[list[KitchenFlowCategory]] = relationship(
        "KitchenFlowCategory",
        back_populates="kitchen_flow",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<KitchenFlow {self.name}>"


class KitchenFlowCategory(Base, UUIDMixin):
    """Which menu categories route to a kitchen flow."""

    __tablename__ = "kitchen_flow_categories"
    __table_args__ = (
        UniqueConstraint(
            "kitchen_flow_id", "category_id", name="uq_kitchen_flow_category"
        ),
    )

    kitchen_flow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kitchen_flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kitchen_flow: Mapped[KitchenFlow] = relationship(
        "KitchenFlow", back_populates="categories"
    )

    def __repr__(self) -> str:
        return f"<KitchenFlowCategory flow={self.kitchen_flow_id}>"
