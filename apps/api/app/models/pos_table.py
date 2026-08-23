from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, status_vocabulary

if TYPE_CHECKING:
    from .branch import Branch


class TableStatusEnum(str, enum.Enum):
    FREE = "free"
    OCCUPIED = "occupied"
    CHECK_PRINTED = "check_printed"
    RESERVED = "reserved"


class Section(Base, UUIDMixin, TimestampMixin):
    """
    A named area of a branch's floor plan — "Ground Floor", "Terrace", "Counter".
    `layout` holds the canvas size plus non-table decorations (labels, barriers)
    drawn by the table-layout editor.
    """

    __tablename__ = "sections"

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
    layout: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    branch: Mapped[Branch] = relationship("Branch", back_populates="sections")
    tables: Mapped[list[PosTable]] = relationship(
        "PosTable",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="PosTable.display_order",
    )

    def __repr__(self) -> str:
        return f"<Section {self.name}>"


class PosTable(Base, UUIDMixin, TimestampMixin):
    """
    A dine-in table. Named `PosTable` because `Table` collides with SQLAlchemy's
    own `Table`; the physical table name is `tables`.

    `status` is denormalised from the open order for fast floor-plan rendering —
    the order remains the source of truth.
    """

    __tablename__ = "tables"

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Migration 138.
    __table_args__ = (status_vocabulary("tables", "status", TableStatusEnum),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=TableStatusEnum.FREE.value,
        index=True,
    )
    accepts_reservations: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Merged tables: children point at the table that owns the joined check.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tables.id", ondelete="SET NULL"), nullable=True
    )
    revenue_center_tag_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="SET NULL"), nullable=True
    )
    # {"x":, "y":, "width":, "height":, "shape": "round"|"square"|"rect", "rotation":}
    layout: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    section: Mapped[Section] = relationship("Section", back_populates="tables")

    def __repr__(self) -> str:
        return f"<PosTable {self.name} ({self.status})>"
