from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def status_vocabulary(
    table: str,
    column: str,
    values: type[enum.Enum],
    *,
    nullable: bool = False,
) -> CheckConstraint:
    """
    The CHECK that holds a string status column to its Python enum.

    Mirrors migration `099_status_vocabulary_checks` (and any later widening)
    so the ORM metadata matches the database. Derived from the enum rather
    than spelled out, so adding a member updates the metadata in the same
    edit — the migration that widens the database CHECK still has to be
    written, but the drift is then visible instead of silent.
    """
    allowed = ", ".join(f"'{member.value}'" for member in values)
    condition = f"{column} IN ({allowed})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"ck_{table}_{column}_allowed")


def business_date_format(table: str) -> CheckConstraint:
    """
    `business_date` is ten characters of YYYY-MM-DD or it is refused.

    Mirrors migration `100_business_date_format_check`. Format only — the
    column should one day be a real `Date`, and until then this pins the shape
    that check numbers, tills and Z-reports all join on. NULL passes, which
    only `orders.business_date` can be.
    """
    return CheckConstraint(
        r"business_date ~ '^\d{4}-\d{2}-\d{2}$'",
        name=f"ck_{table}_business_date_format",
    )


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
