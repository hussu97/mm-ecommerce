"""The plumbing that lets MM push its catalogue OUT to the marketplaces.

The ingest (see `aggregator.py`) mirrors each marketplace's ledger INTO MM. This
is the other direction: MM as the source of truth for the *menu* and the *branch
hours*, reconciled out to every integrator. Three tables carry what that needs and
the storefront catalogue does not:

**Which MM row is which over there.** `catalog_sync_map` — one row per
`(target, branch, MM entity)` holding the integrator's own id for that entity. The
same product has a different id on every outlet (and names have already drifted, so
matching on name is unsafe), so the id map is the load-bearing plumbing. This is the
per-outlet, category/product/modifier-grained sibling of `external_item_map` (which
is keyed by system only, for order reconciliation, not per outlet).

**What the integrator's menu / hours look like right now.**
`aggregator_menu_snapshot` — one row per `(target, branch, kind)` holding the last
read of that outlet's live menu or hours: the provider's `raw` payload, a
channel-neutral `normalized` form, and the computed `diff` against MM's flagged
catalogue. The read side writes it; the drift report reads it. Nothing is pushed
from it — it is the safe, read-only half of the feature.

**The two integrated branches route through Foodics, not the portal.** For Sharjah
and Barsha the aggregator menu is governed by the Foodics `Grubtech` group
(membership) + `Grubtech` price tag (the aggregator price, kept equal to the product
price) — so `target='foodics'` rows are account-level (`branch_id` null) and carry
the group/price-tag ids in `external_parent_id`. For a non-Foodics outlet
(`target` is a marketplace, `branch_id` set) the map holds that portal's item id.

See `services/aggregators/catalog_sync.py` and
`docs/aggregator-catalog-hours-sync-audit.md`.
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.aggregator import AGGREGATOR_CHANNELS
from app.models.base import Base, TimestampMixin, UUIDMixin

# ── Sync targets ──────────────────────────────────────────────────────────────
# Where a menu/hours write lands. The five marketplaces plus Foodics — the master
# for the two integrated branches, reached through its `Grubtech` group + price
# tag. `grubops` is deliberately NOT a target: MM drives Foodics, and GrubTech
# propagates from there. String + CHECK, spelled out here and mirrored in the
# migration (canon rule 6); a sixth marketplace is an edit here, not a type.
TARGET_FOODICS = "foodics"
SYNC_TARGETS: tuple[str, ...] = (*AGGREGATOR_CHANNELS, TARGET_FOODICS)
_TARGETS_SQL = ", ".join(f"'{t}'" for t in SYNC_TARGETS)

# ── MM entity kinds ───────────────────────────────────────────────────────────
# Which side of the catalogue a map/state row points at. One typed FK per kind
# (like `external_item_map`), with a CHECK that exactly the matching one is set.
KIND_CATEGORY = "category"
KIND_PRODUCT = "product"
KIND_MODIFIER = "modifier"  # an option group
KIND_OPTION = "option"  # one option within a group
MM_KINDS: tuple[str, ...] = (KIND_CATEGORY, KIND_PRODUCT, KIND_MODIFIER, KIND_OPTION)
_KINDS_SQL = ", ".join(f"'{k}'" for k in MM_KINDS)

# ── Snapshot kinds ────────────────────────────────────────────────────────────
SNAPSHOT_MENU = "menu"
SNAPSHOT_HOURS = "hours"
SNAPSHOT_KINDS: tuple[str, ...] = (SNAPSHOT_MENU, SNAPSHOT_HOURS)
_SNAPSHOT_KINDS_SQL = ", ".join(f"'{k}'" for k in SNAPSHOT_KINDS)

# ── Where the read came from ──────────────────────────────────────────────────
SOURCE_HTTP = "http"  # TLS-impersonated provider call
SOURCE_BROWSER = "browser"  # headed Chrome under Xvfb (the ingest's engine)
SOURCE_FOODICS_API = "foodics_api"  # Foodics console API (group/price-tag reads)
SOURCE_MANUAL = "manual"  # captured by hand / imported
SNAPSHOT_SOURCES: tuple[str, ...] = (
    SOURCE_HTTP,
    SOURCE_BROWSER,
    SOURCE_FOODICS_API,
    SOURCE_MANUAL,
)
_SOURCES_SQL = ", ".join(f"'{s}'" for s in SNAPSHOT_SOURCES)

# ── Read status ───────────────────────────────────────────────────────────────
SNAPSHOT_OK = "ok"
SNAPSHOT_STALE = "stale"  # served the last read; a fresh fetch failed/skipped
SNAPSHOT_ERROR = "error"
SNAPSHOT_STATUSES: tuple[str, ...] = (SNAPSHOT_OK, SNAPSHOT_STALE, SNAPSHOT_ERROR)
_SNAPSHOT_STATUSES_SQL = ", ".join(f"'{s}'" for s in SNAPSHOT_STATUSES)

# ── How the id match was made ─────────────────────────────────────────────────
METHOD_EXACT = "exact"
METHOD_FUZZY = "fuzzy"
METHOD_MANUAL = "manual"
MATCH_METHODS: tuple[str, ...] = (METHOD_EXACT, METHOD_FUZZY, METHOD_MANUAL)
_METHODS_SQL = ", ".join(f"'{m}'" for m in MATCH_METHODS)


class CatalogSyncMap(Base, UUIDMixin, TimestampMixin):
    """One MM catalogue entity, as one integrator (per outlet) knows it.

    The missing per-outlet identity plumbing: `(target, branch, MM entity) →
    integrator id`. Seeded from a first full read of each integrator's menu, then
    maintained on create. Like `external_item_map`, a fuzzy/first-seen row does
    nothing until a human sets `approved` — an unapproved row is a proposal in a
    review queue, and the writer (a later phase) acts only on approved rows.
    """

    __tablename__ = "catalog_sync_map"

    #: Which integrator, and — for a marketplace target — which outlet. `foodics`
    #: rows are account-level and leave `branch_id` null (the `Grubtech` group and
    #: price tag serve both integrated branches at once).
    target: Mapped[str] = mapped_column(String(20), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
    )

    #: Which side of the MM catalogue this points at, and the one typed FK that
    #: must match it. Exactly one of the four is set (CHECK below).
    mm_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    modifier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifiers.id", ondelete="CASCADE"),
        nullable=True,
    )
    modifier_option_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("modifier_options.id", ondelete="CASCADE"),
        nullable=True,
    )

    #: The integrator's own id for this entity — a Foodics product/category id, a
    #: Talabat item id, a Careem catalog item id.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The parent scope over there: a Foodics `Grubtech` subgroup id / price-tag
    #: entry id, or a channel category id. Null where the id is globally unique.
    external_parent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: A normalised secondary match key (the integrator's item name, lower-cased)
    #: for re-matching if the id churns. Review-only; the writer keys on the id.
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The verbatim integrator display name, for the review screen.
    external_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: The off switch for one mapping (mirrors `aggregator_branch_map.is_active`).
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    #: The review gate — nothing is pushed against an unapproved row.
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_method: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=METHOD_FUZZY
    )
    match_score: Mapped[Any | None] = mapped_column(Numeric(5, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # One mapping per (target, outlet, MM entity). NULLS NOT DISTINCT so the
        # three unset FK columns and a null branch don't defeat the uniqueness.
        UniqueConstraint(
            "target",
            "branch_id",
            "mm_kind",
            "category_id",
            "product_id",
            "modifier_id",
            "modifier_option_id",
            name="uq_catalog_sync_map_entity",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            f"target IN ({_TARGETS_SQL})", name="ck_catalog_sync_map_target"
        ),
        CheckConstraint(f"mm_kind IN ({_KINDS_SQL})", name="ck_catalog_sync_map_kind"),
        CheckConstraint(
            f"match_method IN ({_METHODS_SQL})", name="ck_catalog_sync_map_method"
        ),
        # Exactly one typed FK is set, and it matches `mm_kind`.
        CheckConstraint(
            "( (category_id IS NOT NULL)::int + (product_id IS NOT NULL)::int "
            "+ (modifier_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int"
            " ) = 1 "
            "AND (category_id IS NULL OR mm_kind = 'category') "
            "AND (product_id IS NULL OR mm_kind = 'product') "
            "AND (modifier_id IS NULL OR mm_kind = 'modifier') "
            "AND (modifier_option_id IS NULL OR mm_kind = 'option')",
            name="ck_catalog_sync_map_one_entity",
        ),
        Index("ix_catalog_sync_map_target_branch", "target", "branch_id"),
        Index("ix_catalog_sync_map_product", "product_id"),
    )

    def __repr__(self) -> str:
        return f"<CatalogSyncMap {self.target} {self.mm_kind} {self.external_id!r}>"


class AggregatorMenuSnapshot(Base, UUIDMixin, TimestampMixin):
    """The last read of one integrator outlet's live menu (or hours), plus the diff.

    Written by the read side, read by the drift report — the safe half of the
    feature. `raw` is the provider's payload verbatim (for debugging a lossy
    parse); `normalized` is the channel-neutral shape the diff engine consumes;
    `diff` is the computed delta against MM's sync-flagged catalogue (missing /
    extra / price / name / modifier), so the report renders without re-deriving.
    Keyed `(target, branch, kind)` so a re-read upserts rather than piling up;
    `foodics` menu snapshots are account-level (`branch_id` null).
    """

    __tablename__ = "aggregator_menu_snapshot"

    target: Mapped[str] = mapped_column(String(20), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=True,
    )
    #: `menu` or `hours` — one table, two reads.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: How the read was obtained (http provider / headed browser / Foodics API).
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SNAPSHOT_OK
    )

    #: The provider's payload verbatim, the channel-neutral normalised form, and
    #: the computed diff against MM. JSONB so the report is a read, not a re-fetch.
    raw: Mapped[dict[str, Any] | list | None] = mapped_column(JSONB, nullable=True)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "target",
            "branch_id",
            "kind",
            name="uq_aggregator_menu_snapshot",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            f"target IN ({_TARGETS_SQL})", name="ck_aggregator_menu_snapshot_target"
        ),
        CheckConstraint(
            f"kind IN ({_SNAPSHOT_KINDS_SQL})", name="ck_aggregator_menu_snapshot_kind"
        ),
        CheckConstraint(
            f"source IN ({_SOURCES_SQL})", name="ck_aggregator_menu_snapshot_source"
        ),
        CheckConstraint(
            f"status IN ({_SNAPSHOT_STATUSES_SQL})",
            name="ck_aggregator_menu_snapshot_status",
        ),
        Index("ix_aggregator_menu_snapshot_target_branch", "target", "branch_id"),
    )

    def __repr__(self) -> str:
        return f"<AggregatorMenuSnapshot {self.target} {self.kind} {self.status}>"
