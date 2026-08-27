"""What the console sends and receives about the GrubOps map."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GrubOpsLocationResponse(BaseModel):
    """One branch and whether its stock is mirrored onto the aggregators."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    branch_name: str | None = None
    branch_reference: str | None = None
    grubops_location_id: str
    grubops_partner_id: str
    is_active: bool


class GrubOpsLocationUpdate(BaseModel):
    """The per-branch switch, and the location it points at.

    `is_active` is the one that gets used: a branch whose register is not live
    yet has nothing true to say about its stock, so it is mapped and left off
    until it does.
    """

    is_active: bool | None = None
    grubops_location_id: str | None = Field(default=None, max_length=64)


class GrubOpsOrderRow(BaseModel):
    """One ingested aggregator order, as the monitoring screen shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    grubops_order_id: str
    external_id: str | None = None
    source_channel: str | None = None
    location_id: str | None = None
    mm_order_id: uuid.UUID | None = None
    #: The MM order number, resolved for the link. Null until the order is
    #: created (or if creation failed).
    mm_order_number: str | None = None
    last_grubops_status: str | None = None
    last_pushed_status: str | None = None
    last_push_error: str | None = None
    #: Whether any line on the created order could not be matched to a product
    #: or option — the thing worth eyeballing.
    has_unmapped_lines: bool = False
    created_at: datetime
    updated_at: datetime


class GrubOpsOrderList(BaseModel):
    """A page of ingested orders, with the counts the screen puts in its header."""

    items: list[GrubOpsOrderRow]
    total: int
    error_count: int
    unmapped_count: int
