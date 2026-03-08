from __future__ import annotations

import uuid
import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDMixin, utcnow
from sqlalchemy import DateTime
from datetime import datetime

if TYPE_CHECKING:
    from .user import User


class RegionEnum(str, enum.Enum):
    DUBAI = "dubai"
    SHARJAH = "sharjah"
    AJMAN = "ajman"
    ABU_DHABI = "abu_dhabi"
    FUJAIRAH = "fujairah"
    RAS_AL_KHAIMAH = "ras_al_khaimah"
    UMM_AL_QUWAIN = "umm_al_quwain"
    AL_AIN = "al_ain"
    REST_OF_UAE = "rest_of_uae"


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
    region: Mapped[str] = mapped_column(String(30), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="AE")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="addresses")

    def __repr__(self) -> str:
        return f"<Address {self.label} - {self.region}>"
