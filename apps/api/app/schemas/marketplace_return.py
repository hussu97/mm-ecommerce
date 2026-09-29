"""The return PIN and hand-back state of a marketplace-cancelled order."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.marketplace_return import MarketplaceReturn


class MarketplaceReturnInfo(BaseModel):
    """What the register and the admin show for an order coming back.

    `status`:
    - `pin_pending` — cancelled after it left the kitchen; the PIN is not read yet.
    - `awaiting_return` — the rider is bringing it back; give them `return_pin`.
    - `received` — taken back in (`received_at` / `received_by`).
    - `not_returning` — the marketplace says nothing is coming back.
    """

    status: Literal["pin_pending", "awaiting_return", "received", "not_returning"]
    channel: str
    external_order_id: str
    return_pin: str | None = None
    cancel_owner: str | None = None
    cancel_reason: str | None = None
    cancelled_at: datetime | None = None
    received_at: datetime | None = None
    received_by: str | None = None
    #: Whether receiving it put the consumed goods back on hand.
    restocked: bool = False

    @classmethod
    def of(cls, row: MarketplaceReturn | None) -> MarketplaceReturnInfo | None:
        if row is None:
            return None
        return cls(
            status=row.status,
            channel=row.channel,
            external_order_id=row.external_order_id,
            return_pin=row.return_pin,
            cancel_owner=row.cancel_owner,
            cancel_reason=row.cancel_reason,
            cancelled_at=row.cancelled_at,
            received_at=row.received_at,
            received_by=row.received_by_label,
            restocked=row.restock_transaction_id is not None,
        )
