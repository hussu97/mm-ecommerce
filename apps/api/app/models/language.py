from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin


class Language(Base):
    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    native_name: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False, default="ltr")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Language {self.code}>"


class UiTranslation(Base, UUIDMixin):
    __tablename__ = "ui_translations"
    __table_args__ = (
        UniqueConstraint("locale", "namespace", "key", name="uq_ui_translation"),
    )

    locale: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("languages.code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    #: When a human last edited this string in the admin console. The i18n seeder
    #: runs on every boot and overwrites any row whose value differs from the
    #: source constant — which silently reverted every console edit (F-ADM-5).
    #: Set, it marks the row as human-owned, and the seeder leaves its value
    #: alone. Null means the string is still source-managed.
    hand_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<UiTranslation {self.locale}:{self.namespace}.{self.key}>"
