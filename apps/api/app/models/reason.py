from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ReasonTypeEnum(str, enum.Enum):
    """Which workflow a reason can be selected in."""

    VOID_RETURN = "void_return"
    QUANTITY_ADJUSTMENT = "quantity_adjustment"
    DRAWER_OPERATION = "drawer_operation"


class Reason(Base, UUIDMixin, TimestampMixin):
    """
    A pre-defined justification the cashier must pick when voiding, returning,
    adjusting stock, or moving cash in/out of the drawer. Reporting groups
    voids and drawer movements by reason, so these are not free text.
    """

    __tablename__ = "reasons"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(150), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Reason {self.type}:{self.name}>"
