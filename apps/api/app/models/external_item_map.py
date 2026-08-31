"""One external system's item → this catalogue's product (or option).

The generalised sibling of `grubops_item_map`: instead of being bound to GrubOps,
one row maps an item as some *external system* names it — an aggregator's scraped
item name (Keeta, Deliveroo, …), a GrubOps recipe id, a Foodics sku — to an MM
`Product` or `ModifierOption`. `system` says which world the `external_ref` lives
in, so a single table serves every integration and the same catalogue product can
carry a different name in each.

Matching an external name to a product is a *guess*, so — exactly like the GrubOps
map — nothing acts on a row until a human sets `approved`: the aggregator promotion
resolver reads only approved rows, and an unapproved row is a proposal sitting in a
review queue. `match_method`/`match_score` record how the guess was made; a manual
edit marks the row `manual`. `external_name` is the verbatim display string for that
review screen — the resolver keys on `external_ref` (the normalised match key), not
on it, because names drift and the key does not.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin

#: The external worlds an item id/name can come from. A controlled internal
#: vocabulary (rule 6: String + CHECK), spelled out here and mirrored in the
#: migration. The aggregator channels plus the two POS-side systems, so the one
#: table can eventually hold GrubOps/Foodics mappings too.
EXTERNAL_SYSTEMS: tuple[str, ...] = (
    "grubops",
    "foodics",
    "careem",
    "deliveroo",
    "talabat",
    "noon",
    "keeta",
)
_SYSTEMS_SQL = ", ".join(f"'{s}'" for s in EXTERNAL_SYSTEMS)

KIND_PRODUCT = "product"
KIND_OPTION = "option"
#: A menu category. Added for the catalog-&-hours sync, which needs to map an
#: aggregator's category to an MM `Category` (order reconciliation only ever
#: needed product/option). One map for the whole catalogue — categories,
#: products and options — rather than a second table that could drift.
KIND_CATEGORY = "category"
MM_KINDS = (KIND_PRODUCT, KIND_OPTION, KIND_CATEGORY)

METHOD_EXACT = "exact"
METHOD_FUZZY = "fuzzy"
METHOD_MANUAL = "manual"
MATCH_METHODS = (METHOD_EXACT, METHOD_FUZZY, METHOD_MANUAL)

#: GrubOps' own item types, carried on `external_type` (null for name-keyed
#: systems). RECIPE → a product; MODIFIER/NESTED_MODIFIER → an option.
TYPE_RECIPE = "RECIPE"
TYPE_MODIFIER = "MODIFIER"
TYPE_NESTED_MODIFIER = "NESTED_MODIFIER"
EXTERNAL_TYPES = (TYPE_RECIPE, TYPE_MODIFIER, TYPE_NESTED_MODIFIER)


class ExternalItemMap(Base, UUIDMixin, TimestampMixin):
    """One external item identifier, mapped to a catalogue product or option."""

    __tablename__ = "external_item_map"

    #: Which external system `external_ref` belongs to.
    system: Mapped[str] = mapped_column(String(20), nullable=False)
    #: An optional scope the ids live under — a GrubOps brand id, say. Null for a
    #: system (like the aggregators) whose ref is globally unique. Carried on the
    #: identity key and echoed back on the push-out payload.
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The primary external match key — the normalised item name for a scraped
    #: aggregator line, or the recipe id for a GrubOps item. What the resolver joins
    #: a product on.
    external_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    #: A second external id for a composite identity — a GrubOps modifier id under
    #: its recipe (the modifier id alone is ambiguous). Null for name-keyed systems.
    external_sub_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: A third id for a nested identity — a GrubOps nested/child modifier. Null
    #: otherwise.
    external_child_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The external system's own item type where it has one — GrubOps
    #: RECIPE/MODIFIER/NESTED_MODIFIER. Null for systems keyed only by name.
    external_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: The verbatim external display name, for the review screen only.
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Which side of the catalogue this maps to — `product`, `option` or (for the
    #: catalog sync) `category`. Exactly one of the FKs below matches it, or none
    #: for a proposal we have seen but not yet mapped.
    mm_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=KIND_PRODUCT
    )
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
    #: The MM category, when `mm_kind='category'`. Null otherwise.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
    )

    #: The gate. A row does nothing until a human approves it — the resolver reads
    #: only approved rows; an unapproved one is a proposal awaiting review.
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default=METHOD_FUZZY
    )
    match_score: Mapped[Any | None] = mapped_column(Numeric(5, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # One row per external identity. The identity is the whole composite key,
        # not just `external_ref`, so a GrubOps modifier (recipe + modifier id) is
        # distinct from its recipe, and NULLS NOT DISTINCT (Postgres 15+) makes the
        # name-keyed aggregator case — sub/child/scope all null — behave like a
        # plain `(system, external_ref)` unique.
        UniqueConstraint(
            "system",
            "scope",
            "external_ref",
            "external_sub_ref",
            "external_child_ref",
            name="uq_external_item_map_identity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            f"system IN ({_SYSTEMS_SQL})", name="ck_external_item_map_system"
        ),
        CheckConstraint(
            "mm_kind IN ('product', 'option', 'category')",
            name="ck_external_item_map_kind",
        ),
        CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual')",
            name="ck_external_item_map_method",
        ),
        CheckConstraint(
            "external_type IS NULL OR external_type IN "
            "('RECIPE', 'MODIFIER', 'NESTED_MODIFIER')",
            name="ck_external_item_map_type",
        ),
        # At most one catalogue entity, and it must match `mm_kind` when set. A row
        # with none set is a proposal for a name we have seen but not yet mapped.
        CheckConstraint(
            "( (product_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int "
            "+ (category_id IS NOT NULL)::int ) <= 1 "
            "AND (product_id IS NULL OR mm_kind = 'product') "
            "AND (modifier_option_id IS NULL OR mm_kind = 'option') "
            "AND (category_id IS NULL OR mm_kind = 'category')",
            name="ck_external_item_map_one_entity",
        ),
        Index("ix_external_item_map_system_approved", "system", "approved"),
    )

    def __repr__(self) -> str:
        return f"<ExternalItemMap {self.system} {self.external_ref!r}>"
