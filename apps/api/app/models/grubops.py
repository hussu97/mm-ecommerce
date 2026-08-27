"""
What GrubOps calls the things this shop already has names for.

The aggregators — Noon, Talabat, Deliveroo — are fed from GrubTech's GrubOps
console, so an item that runs out has to be said twice: once on the terminal,
once in GrubOps. These tables are what let the second one be said by a machine.

Two tables here, plus one that has moved out.

**Which branches exist over there.** `grubops_location_map`. Only Sharjah and
Barsha Heights trade on GrubOps; Karama and DSO do not. A branch with no row is
simply never enumerated — the same "a row is an exception" idiom
`branch_products` uses, and it means the two off-platform branches cost no code
rather than a special case.

**Which item is which** used to live here as `grubops_item_map`; it was folded
into the generalized `external_item_map` (system `grubops`), which serves every
integration's item mappings from one table. The matcher and the sync read/write
it there now; a GrubOps recipe is its `external_ref`, a modifier its
`external_sub_ref`, the brand its `scope`. Identity is deliberately **not**
per-branch — a `recipeId` is catalogue-wide (`brandIds=ALL, locationIds=ALL`) and
the location is supplied per call from the table above — so a product sold at both
branches is one mapping pushed to two locations.

**What we last told them.** `grubops_sync_state`. The reconcile loop pushes
differences, not state, and without a record of the last push every tick would
resend the whole menu. It is also the seam between the two write paths: the
immediate push and the loop both stamp it, so the loop sees no delta for a
change the terminal already sent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

#: Which of our two catalogues a mapping row points at. Spelled out here and in
#: the migration's CHECK, per the string-plus-constraint convention — a native
#: enum would need a migration of its own to grow.
KIND_PRODUCT = "product"
KIND_OPTION = "option"
MM_KINDS: tuple[str, ...] = (KIND_PRODUCT, KIND_OPTION)

#: GrubOps' own word for what a menu line is. `NESTED_MODIFIER` is an option
#: hanging off another option; we only produce it if the seeder finds one.
TYPE_RECIPE = "RECIPE"
TYPE_MODIFIER = "MODIFIER"
TYPE_NESTED_MODIFIER = "NESTED_MODIFIER"
GRUBOPS_TYPES: tuple[str, ...] = (TYPE_RECIPE, TYPE_MODIFIER, TYPE_NESTED_MODIFIER)

#: How a row came to exist. Kept because "the computer matched these two names
#: and scored it 0.86" is the sort of thing somebody wants to see the day a
#: wrong item goes dark on Talabat.
MATCH_EXACT = "exact"
MATCH_FUZZY = "fuzzy"
MATCH_MANUAL = "manual"
MATCH_METHODS: tuple[str, ...] = (MATCH_EXACT, MATCH_FUZZY, MATCH_MANUAL)


class GrubOpsLocationMap(Base, UUIDMixin, TimestampMixin):
    """One of our branches, as GrubOps knows it."""

    __tablename__ = "grubops_location_map"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    grubops_location_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Carried per row rather than read from settings so a second partner — a
    #: second brand on the same console — needs no code.
    grubops_partner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The off switch for one branch. Clearing it stops the sync for that
    #: location without destroying the mapping that would have to be rebuilt.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    def __repr__(self) -> str:
        return f"<GrubOpsLocationMap branch={self.branch_id} loc={self.grubops_location_id}>"


class GrubOpsSyncState(Base, UUIDMixin, TimestampMixin):
    """
    The last thing we told GrubOps about one item at one branch.

    Per branch as well as per item because the same recipe is out at Sharjah and
    on the shelf at Barsha Heights often enough that a single row would make the
    two locations fight over it.

    `last_pushed_until` is stored so a timed window can be compared rather than
    resent: `end_of_day` recomputes to a very slightly different moment on every
    tick, and without the stored value the loop would push the same fact all
    evening.
    """

    __tablename__ = "grubops_sync_state"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The item this sync-state is for — now a row in the generalized
    #: `external_item_map` (system `grubops`), since GrubOps' own item map was
    #: folded into it. Ids were preserved on the merge, so old rows still resolve.
    external_item_map_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_item_map.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Null means "never pushed", which is not the same as "pushed available"
    #: — the first tick after a mapping is approved has to say something.
    last_pushed_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_pushed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Left set after a failure and cleared on the next success, so the review
    #: screen can show which items are currently not getting through.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "branch_id", "external_item_map_id", name="uq_grubops_sync_state"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GrubOpsSyncState branch={self.branch_id} "
            f"available={self.last_pushed_available}>"
        )
