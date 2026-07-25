"""
Which branch delivers to which zone.

A many-to-many rather than a column on either side: a zone with no branch is
simply unserviced, and a zone served by two branches is a real overlap that
the dispatcher resolves rather than a data error to prevent.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class BranchRegion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "branch_regions"
    __table_args__ = (
        UniqueConstraint("branch_id", "region_id", name="uq_branch_region"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
    )
    #: Overrides the region's own fee for this branch; null falls back to it.
    delivery_fee: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
