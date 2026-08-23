"""
What GrubOps calls the things this shop already has names for.

The aggregators — Noon, Talabat, Deliveroo — are fed from GrubTech's GrubOps
console, so an item that runs out has to be said twice: once on the terminal,
once in GrubOps. These tables are what let the second one be said by a machine.

Three tables, because there are three separate questions and conflating any two
of them is how a sync starts lying.

**Which branches exist over there.** `grubops_location_map`. Only Sharjah and
Barsha Heights trade on GrubOps; Karama and DSO do not. A branch with no row is
simply never enumerated — the same "a row is an exception" idiom
`branch_products` uses, and it means the two off-platform branches cost no code
rather than a special case.

**Which item is which.** `grubops_item_map`. GrubOps keys its menu by
`recipeId`/`modifierId` and knows nothing about our uuids, so the join between
the two catalogues has to be stored. It is built by matching on name, which is
a guess, so every row carries how it was matched and nobody acts on it until a
human has set `approved`. An unapproved row is inert: the sync reads
`WHERE approved` and skips the rest.

Identity here is deliberately **not** per-branch. A `recipeId` is catalogue-wide
— the login's own token says `brandIds=ALL, locationIds=ALL` — and the location
is supplied per call from the table above. So a product sold at both branches is
one row pushed to two locations, and a mapping fixed once is fixed everywhere.

**What we last told them.** `grubops_sync_state`. The reconcile loop pushes
differences, not state, and without a record of the last push every tick would
resend the whole menu. It is also the seam between the two write paths: the
immediate push and the loop both stamp it, so the loop sees no delta for a
change the terminal already sent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
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


class GrubOpsItemMap(Base, UUIDMixin, TimestampMixin):
    """
    One product or one modifier option, and the GrubOps item it is.

    Exactly one of `product_id` / `modifier_option_id` is set, and `mm_kind`
    says which — a CHECK enforces the pair so a half-filled row cannot be
    written and then quietly skipped at push time.

    Which of the three GrubOps id columns is filled follows `grubops_type`:
    `RECIPE` uses `grubops_recipe_id`, `MODIFIER` uses `grubops_modifier_id`
    alongside its parent recipe, and `NESTED_MODIFIER` adds
    `grubops_child_modifier_id`. They are separate columns rather than one
    polymorphic `item_id` because the payload GrubOps wants names all three
    separately, and collapsing them here would only mean picking them apart
    again in the provider.
    """

    __tablename__ = "grubops_item_map"

    mm_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
    )
    modifier_option_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=True,
    )

    grubops_brand_id: Mapped[str] = mapped_column(String(64), nullable=False)
    grubops_recipe_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grubops_modifier_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grubops_child_modifier_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    grubops_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: What GrubOps calls it, kept for the review screen only. The sync never
    #: reads it — names drift, ids do not.
    grubops_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    match_method: Mapped[str] = mapped_column(String(16), nullable=False)
    match_score: Mapped[Any | None] = mapped_column(Numeric(5, 2), nullable=True)
    #: The gate. Name matching is a guess and this is where a human says the
    #: guess is right; nothing is ever pushed for an unapproved row.
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(mm_kind = 'product' AND product_id IS NOT NULL "
            "AND modifier_option_id IS NULL) OR "
            "(mm_kind = 'option' AND modifier_option_id IS NOT NULL "
            "AND product_id IS NULL)",
            name="ck_grubops_item_map_one_entity",
        ),
        CheckConstraint(
            "grubops_type IN ('RECIPE', 'MODIFIER', 'NESTED_MODIFIER')",
            name="ck_grubops_item_map_type",
        ),
        CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual')",
            name="ck_grubops_item_map_method",
        ),
        # One GrubOps identity per catalogue entity. Two rows for one product
        # would mean two pushes disagreeing about the same shelf.
        UniqueConstraint("product_id", name="uq_grubops_item_map_product"),
        UniqueConstraint("modifier_option_id", name="uq_grubops_item_map_option"),
        # The sync's own filter, and the review screen's busiest one.
        Index("ix_grubops_item_map_approved", "approved"),
    )

    def __repr__(self) -> str:
        return f"<GrubOpsItemMap {self.mm_kind} type={self.grubops_type} approved={self.approved}>"


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
    grubops_item_map_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grubops_item_map.id", ondelete="CASCADE"),
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
            "branch_id", "grubops_item_map_id", name="uq_grubops_sync_state"
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GrubOpsSyncState branch={self.branch_id} "
            f"available={self.last_pushed_available}>"
        )
