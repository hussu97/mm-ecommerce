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


class GrubOpsMappingResponse(BaseModel):
    """One suggested or confirmed pairing, as the review screen shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mm_kind: str
    product_id: uuid.UUID | None = None
    modifier_option_id: uuid.UUID | None = None
    #: Our name for it, resolved for display — the table joins so the screen
    #: does not have to make a request per row.
    mm_name: str | None = None
    #: The product an option hangs off, so "Pistachio" is not ambiguous.
    mm_parent_name: str | None = None
    grubops_brand_id: str
    grubops_recipe_id: str | None = None
    grubops_modifier_id: str | None = None
    grubops_child_modifier_id: str | None = None
    grubops_type: str
    grubops_name: str | None = None
    match_method: str
    match_score: float | None = None
    approved: bool
    approved_by: str | None = None
    notes: str | None = None
    #: What the sync last managed to tell GrubOps, per branch. Null where it has
    #: never been pushed.
    last_pushed_at: datetime | None = None
    last_error: str | None = None


class GrubOpsMappingUpdate(BaseModel):
    """A correction, or an approval.

    Setting `approved` is the act that puts a row into service; everything else
    here exists so somebody can fix a bad guess before they do.
    """

    approved: bool | None = None
    grubops_recipe_id: str | None = Field(default=None, max_length=64)
    grubops_modifier_id: str | None = Field(default=None, max_length=64)
    grubops_child_modifier_id: str | None = Field(default=None, max_length=64)
    grubops_type: str | None = Field(default=None, max_length=32)
    notes: str | None = None


class GrubOpsMappingList(BaseModel):
    """A page of mappings, with the counts the screen puts in its tabs."""

    items: list[GrubOpsMappingResponse]
    total: int
    approved_count: int
    pending_count: int


class GrubOpsSyncSummary(BaseModel):
    """What one press of "Sync from GrubOps" did."""

    created: int
    refreshed: int
    unmatched_ours: list[str]
    unmatched_theirs: list[str]
    errors: list[str]
