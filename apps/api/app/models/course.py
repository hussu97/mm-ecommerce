"""
Courses — the order food reaches a table in.

A dine-in check is fired in stages: starters now, mains when the table has
finished them. Foodics models this as a course on each line plus a "fire
course" action; without it every send-to-kitchen dumps the whole check on the
pass at once and the mains go cold while the starters are eaten.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Course(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "courses"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: Firing order: starters before mains before dessert.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Course {self.name}>"
