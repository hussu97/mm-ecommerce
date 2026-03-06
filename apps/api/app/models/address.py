from __future__ import annotations

import uuid
import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, utcnow
from sqlalchemy import DateTime
from datetime import datetime

if TYPE_CHECKING:
    from .user import User


class EmirateEnum(str, enum.Enum):
    DUBAI = "Dubai"
    SHARJAH = "Sharjah"
    AJMAN = "Ajman"
    ABU_DHABI = "Abu Dhabi"
    RAS_AL_KHAIMAH = "Ras Al Khaimah"
    FUJAIRAH = "Fujairah"
    UMM_AL_QUWAIN = "Umm Al Quwain"


class Address(Base, UUIDMixin):
    __tablename__ = "addresses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(50), nullable=False, default="Home")
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    emirate: Mapped[EmirateEnum] = mapped_column(
        Enum(
            EmirateEnum,
            name="emirateenum",
            create_type=False,
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="AE")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="addresses")

    def __repr__(self) -> str:
        return f"<Address {self.label} - {self.emirate}>"
