from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, TimestampMixin


class Region(Base, UUIDMixin, TimestampMixin):
    """
    A deliverable region (e.g. Dubai, Sharjah).

    name_translations is a JSONB dict keyed by locale code:
        {"en": "Dubai", "ar": "دبي"}
    """

    __tablename__ = "regions"

    slug: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    name_translations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    delivery_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Region {self.slug} fee={self.delivery_fee}>"

    def name(self, locale: str = "en") -> str:
        return (
            self.name_translations.get(locale)
            or self.name_translations.get("en")
            or self.slug
        )


class DeliverySettings(Base, UUIDMixin, TimestampMixin):
    """
    Global delivery configuration — expected to have exactly one row.
    """

    __tablename__ = "delivery_settings"

    free_delivery_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("200.00")
    )
    pickup_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )

    def __repr__(self) -> str:
        return f"<DeliverySettings threshold={self.free_delivery_threshold}>"
