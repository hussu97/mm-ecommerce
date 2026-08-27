"""The unified item-mapping review queue — one shape for every external system.

Mirrors `external_item_map`: a row ties an external system's item (a scraped
aggregator name, a GrubOps recipe/modifier, a Foodics sku) to one of our products
or options, and carries the review metadata (match method/score, approved). The
GrubOps-specific ids ride the generic `external_ref`/`external_sub_ref`/
`external_child_ref`/`external_type` columns, so the same screen serves them all.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ItemMappingResponse(BaseModel):
    id: uuid.UUID
    system: str
    mm_kind: str
    product_id: uuid.UUID | None = None
    modifier_option_id: uuid.UUID | None = None
    #: Our catalogue names, resolved for the review screen.
    mm_name: str | None = None
    mm_parent_name: str | None = None
    #: The external identity, generic across systems.
    scope: str | None = None
    external_ref: str
    external_sub_ref: str | None = None
    external_child_ref: str | None = None
    external_type: str | None = None
    external_name: str | None = None
    match_method: str
    match_score: float | None = None
    approved: bool
    approved_by: str | None = None
    notes: str | None = None
    #: Last push result — GrubOps only; null for systems that are read-only mirrors.
    last_pushed_at: datetime | None = None
    last_error: str | None = None


class ItemMappingList(BaseModel):
    items: list[ItemMappingResponse]
    total: int
    approved_count: int
    pending_count: int


class ItemMappingUpdate(BaseModel):
    """Correct a guess or approve it. Any field left null is left unchanged.

    Point the row at a catalogue entity with `product_id` **or**
    `modifier_option_id` (with `mm_kind`); edit the external identity with the
    `external_*` fields. Either kind of edit marks the row `manual`.
    """

    product_id: uuid.UUID | None = None
    modifier_option_id: uuid.UUID | None = None
    mm_kind: str | None = None
    external_ref: str | None = None
    external_sub_ref: str | None = None
    external_child_ref: str | None = None
    external_type: str | None = None
    notes: str | None = None
    approved: bool | None = None


class ItemMappingSyncSummary(BaseModel):
    created: int
    refreshed: int
    unmatched_ours: list[str]
    unmatched_theirs: list[str]
    errors: list[str]
