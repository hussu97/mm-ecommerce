"""The link between a GrubOps aggregator order and the MM order it became.

The OOS sync pushes availability *out*; this is the other direction. The ingest
loop (`services/grubops_orders.py`) polls GrubOps for Talabat/Noon/Careem/
Deliveroo/Keeta orders and writes each into the shared `orders` table as
`source='aggregator'`. This table is the ledger that ties the two together: it
is where dedupe happens (an order is ingested once, keyed on GrubOps's own
order id), where the loop remembers the last status it saw so a quiet tick costs
nothing, and where write-back records what it last mirrored out.

One row per GrubOps order. `mm_order_id` is null only in the gap between first
seeing an order and successfully creating its MM row — a creation that failed is
a null to retry, not a duplicate to guard against.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class GrubOpsOrderMap(Base, UUIDMixin, TimestampMixin):
    """One aggregator order as GrubOps holds it, and the MM order it became."""

    __tablename__ = "grubops_order_map"

    #: GrubOps' own order id (`orderInternalId`). The natural key the loop polls
    #: on and the one thing guaranteed unique across every channel.
    grubops_order_id: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    #: The aggregator's own reference (`externalId`) — a Talabat/Noon number a
    #: human quotes. Not unique on its own: two channels can reuse a number.
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The channel name verbatim, provider word, unconstrained: `Talabat`,
    #: `Keeta 2.0`, `Noon`, `Careem`, `Deliveroo`.
    source_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: The GrubOps location this order belongs to, resolved to a branch via
    #: `grubops_location_map`.
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The order's id in **Foodics** — the POS behind GrubTech, and where MM now
    #: drives the order forward (accept/dispatch/close/void) instead of via GrubOps'
    #: force-* overrides. GrubOps does not surface it as a field, so it is parsed
    #: from the ingested `getOrderInfo` payload's history (the code-20000
    #: "…Foodics Order Id: <uuid>" event) and cached here so the write-back needs
    #: no re-parse. Null until that event has been seen for the order.
    foodics_order_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    #: The MM order. Null in the gap between first sight and a successful
    #: create; SET NULL rather than CASCADE so deleting an order does not erase
    #: the fact that it was ingested.
    mm_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    #: The last GrubOps status ingested, so the loop only does work on a change.
    last_grubops_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: What write-back last mirrored *out*, and why it last failed. Mirrors the
    #: `last_pushed_*` / `last_error` pair on `grubops_sync_state`.
    last_pushed_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_push_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: The last full `getOrderInfo` payload — audit, debugging, and the fields
    #: we do not model (driver, delivery, payment settlements).
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<GrubOpsOrderMap grubops={self.grubops_order_id} "
            f"channel={self.source_channel} mm={self.mm_order_id}>"
        )
