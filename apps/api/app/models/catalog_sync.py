"""State the catalog-&-hours sync needs that no existing table already holds.

The ingest (`aggregator.py`) mirrors each marketplace's ledger INTO MM. This is the
other direction — MM as the source of truth for the *menu* and *branch hours*,
reconciled out to every integrator. Deliberately NOT a new mapping table: the
identity plumbing is reused, not duplicated, to avoid drift —

- **which MM entity is which over there** → `external_item_map` (the per-system
  name→product/option/category map, extended with categories for this feature),
- **which branch is which outlet** → `aggregator_branch_map` / `foodics_branch_map`,
- **the integrator's live item ids at write time** → the snapshot below (the read
  carries each item's own id, so no parallel per-outlet id map is stored).

The one genuinely new thing here is **`aggregator_menu_snapshot`**: the last read of
one integrator outlet's live menu (or hours) — the provider's `raw` payload, a
channel-neutral `normalized` form, and the computed `diff` against MM's flagged
catalogue. The read side writes it; the drift report reads it; the writer resolves
live ids from it. `foodics` menu snapshots are account-level (`branch_id` null,
because the `Grubtech` group + price tag serve both integrated branches at once).

See `services/aggregators/catalog_sync.py` and
`docs/integrators-and-aggregators.md`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.aggregator import AGGREGATOR_CHANNELS
from app.models.base import Base, TimestampMixin, UUIDMixin

# ── Outbox: a product change that must fan out to the integrators ──────────────
OUTBOX_PENDING = "pending"
OUTBOX_DONE = "done"
OUTBOX_ERROR = "error"
OUTBOX_STATUSES: tuple[str, ...] = (OUTBOX_PENDING, OUTBOX_DONE, OUTBOX_ERROR)
_OUTBOX_STATUSES_SQL = ", ".join(f"'{s}'" for s in OUTBOX_STATUSES)

# ── Sync targets ──────────────────────────────────────────────────────────────
# Where a menu/hours write lands. The five marketplaces plus Foodics — the master
# for the two integrated branches, reached through its `Grubtech` group + price
# tag. `grubops` is deliberately NOT a target: MM drives Foodics, and GrubTech
# propagates from there. These are a SUBSET of `external_item_map.EXTERNAL_SYSTEMS`,
# so the same `system`/`target` string keys both — one vocabulary, no drift.
TARGET_FOODICS = "foodics"
SYNC_TARGETS: tuple[str, ...] = (*AGGREGATOR_CHANNELS, TARGET_FOODICS)
_TARGETS_SQL = ", ".join(f"'{t}'" for t in SYNC_TARGETS)

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


class AggregatorMenuSnapshot(Base, UUIDMixin, TimestampMixin):
    """The last read of one integrator outlet's live menu (or hours), plus the diff.

    Written by the read side, read by the drift report and the writer — the safe
    half of the feature. `raw` is the provider's payload verbatim (for debugging a
    lossy parse); `normalized` is the channel-neutral shape the diff engine
    consumes (and the source of each item's live channel id at write time); `diff`
    is the computed delta against MM's sync-flagged catalogue (missing / extra /
    price / name / modifier), so the report renders without re-deriving. Keyed
    `(target, branch, kind)` so a re-read upserts rather than piling up; `foodics`
    menu snapshots are account-level (`branch_id` null).
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


class CatalogSyncOutbox(Base, UUIDMixin, TimestampMixin):
    """One "this product changed — fan it out to the integrators" job.

    Written **in the same transaction** as the product create/update (so a save
    that rolls back never leaves a phantom job), then drained by the catalog-sync
    scheduler which runs an idempotent create-or-update of the product on every
    integrator it belongs on (name EN/AR, description EN/AR, price, image). This is
    the system half of "menu sync is driven from the admin, not a script": the
    admin just saves the product; the sync is a consequence, not a separate action.

    Deliberately a durable row, not an in-process task: the sync opens marketplace
    sessions and the Foodics console, must survive a redeploy/rollback, and must
    retry — none of which a fire-and-forget task gives. `sync_product` is idempotent,
    so a duplicate row is harmless (it just re-asserts parity)."""

    __tablename__ = "catalog_sync_outbox"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: `created` / `updated` — provenance for the log; both drain the same way.
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=OUTBOX_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The per-target result of the last drain, for the admin/console + debugging.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_OUTBOX_STATUSES_SQL})",
            name="ck_catalog_sync_outbox_status",
        ),
        # One open job per product: rapid successive edits coalesce to a single
        # pending row (the drain always reads the product's current state), so a
        # burst of saves is one sync, not N.
        Index(
            "uq_catalog_sync_outbox_pending",
            "product_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<CatalogSyncOutbox {self.product_id} {self.status}>"
