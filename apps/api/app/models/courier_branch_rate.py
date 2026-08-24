"""What one courier charges at one branch, where that differs from its default.

A courier's rates live on its `couriers` row and are the same wherever it
carries. Deliveroo broke that assumption: it takes 27% of a Sharjah basket and
31% of a Barsha one, negotiated branch by branch. Rather than teach every rate
column to be two numbers, this table holds the *exceptions* — one row per
(courier, branch) pair that differs — and `order_fees` reads it first, falling
back to the courier's own row for anything it does not override.

**Override, not replacement.** A null column here means "no special rate at this
branch, use the courier default", not "this branch is free". So a Deliveroo row
setting only `commission_percent` leaves the courier's payment-fee rules exactly
as they are; the two are read together the same way a website order's fee is.
The VAT-inclusive / net-of-base / cash-exempt *grammar* is not repeated here —
it is a property of the contract, not the branch, and stays on the courier row.

**Only meaningful for an aggregator.** A dispatch courier is billed per booking
on `order_deliveries.cost_total`, never a percentage, so a branch override on
one would be read by nothing. Nothing enforces that in the schema — it is a
label, like `couriers.is_aggregator` — but a row for a dispatch courier is a
mistake, not a feature.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class CourierBranchRate(Base, UUIDMixin, TimestampMixin):
    """A per-branch override of a courier's commission / payment-fee rates."""

    __tablename__ = "courier_branch_rate"
    __table_args__ = (
        UniqueConstraint("courier_id", "branch_id", name="uq_courier_branch_rate"),
    )

    #: The courier whose default this row bends. CASCADE: an override is
    #: meaningless once its courier is gone.
    courier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("couriers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The branch this rate applies at. CASCADE for the same reason.
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Each mirrors the same-named column on `couriers`, and each is null unless
    #: this branch genuinely differs. Same `_percent` / `_fixed` conventions,
    #: same before-VAT quoting — the grammar flags on the courier row decide how
    #: they are read, whichever branch the number came from.
    commission_percent: Mapped[Any | None] = mapped_column(Numeric(5, 2), nullable=True)
    commission_fixed: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)
    payment_fee_percent: Mapped[Any | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    payment_fee_fixed: Mapped[Any | None] = mapped_column(Numeric(10, 2), nullable=True)

    def __repr__(self) -> str:
        return f"<CourierBranchRate courier={self.courier_id} branch={self.branch_id}>"
