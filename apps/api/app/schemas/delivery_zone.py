"""
The per-zone branch list the admin edits: which branches serve a zone, in what
order, each with its own courier and escapes.

A zone used to name one kitchen and one courier. It now carries an *ordered*
list of branches — `PolygonBranchFulfilment` — and the checkout walks that list
in rank order, giving the order to the first branch that can make the whole
basket. These are the shapes the delivery-zones admin uses to read and rewrite
that list. The three single-value columns on `delivery_polygons`
(`branch_id`, `fulfilment_provider`, `alternate_providers`) are the rank-1
mirror of this list, kept truthful whenever the list is set.

Kept in `app/schemas/` rather than beside the route (CLAUDE.md): a schema
declared inline is invisible to everything that reads `app/schemas/` to learn
the shape of the API.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.polygon_branch_fulfilment import PolygonBranchFulfilment


class BranchAssignmentInput(BaseModel):
    """One branch's standing in a zone, as the admin sets it."""

    branch_id: uuid.UUID
    #: 1 is preferred. The whole submitted set must be dense and unique
    #: (1..N) — validated in the handler, not here, so the error names the zone
    #: and reads like the others in this router rather than a 422 field trace.
    rank: int = Field(ge=1)
    #: The courier this branch uses for this zone. Checked against
    #: `FulfilmentProviderEnum` in the handler, the same way the polygon and
    #: courier edit routes check theirs — text with a CHECK, not a native enum.
    fulfilment_provider: str
    #: The couriers an order this branch fulfils may be moved to when its
    #: preferred one will not carry it. Replaces the list wholesale; empty is a
    #: valid "no escapes".
    alternate_providers: list[str] = Field(default_factory=list)


class BranchAssignmentSet(BaseModel):
    """The complete ordered branch list for a zone, replacing whatever is there.

    Set as a whole rather than a row at a time because the invariant is a
    property of the set — ranks dense and unique from 1, each branch once — and
    editing one row at a time would step through states that violate it. This is
    the create/update/delete of assignments in one atomic replace.
    """

    assignments: list[BranchAssignmentInput]


class BranchAssignmentResponse(BaseModel):
    """One assignment row, as the admin screens read it back."""

    id: str
    polygon_id: str
    branch_id: str
    rank: int
    fulfilment_provider: str
    alternate_providers: list[str]

    @classmethod
    def of(cls, a: PolygonBranchFulfilment) -> "BranchAssignmentResponse":
        return cls(
            id=str(a.id),
            polygon_id=str(a.polygon_id),
            branch_id=str(a.branch_id),
            rank=a.rank,
            fulfilment_provider=a.fulfilment_provider,
            alternate_providers=list(a.alternate_providers or []),
        )
