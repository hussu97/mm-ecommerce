"""Catalog & hours sync scaffold: identity map, menu snapshots, weekly hours.

The read/model half of the central catalog-&-hours sync (writes stay behind
`CATALOG_SYNC_ENABLED`, off). All additive — three new tables and two pairs of
nullable columns, nothing existing is touched:

1. `catalog_sync_map` — the missing per-outlet identity plumbing:
   `(target, branch, MM entity) → integrator id`. Foodics rows are account-level
   (branch null); marketplace rows are per outlet. Like `external_item_map`, an
   unapproved row is a proposal awaiting review.
2. `aggregator_menu_snapshot` — the last read of one outlet's live menu/hours
   (raw + normalized + computed diff), read by the drift report.
3. `branch_weekly_hours` — a canonical per-day, multi-shift schedule MM has not
   had; the source of truth the hours writer (later phase) fans out per portal.
4. `products` / `categories` gain `sync_to_aggregators` + `sync_channels` — the
   per-row "goes to the aggregators" switch, deliberately separate from
   `sales_channels` (the audit found the two disagree for 16 signature products).

See `docs/aggregator-catalog-hours-sync-audit.md`.

Revision ID: 171_catalog_sync_scaffold
Revises: 170_order_marketing_fee
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "171_catalog_sync_scaffold"
down_revision: Union[str, None] = "170_order_marketing_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TARGETS = "'careem', 'deliveroo', 'talabat', 'noon', 'keeta', 'foodics'"


def upgrade() -> None:
    # 1. catalog_sync_map — per-outlet identity map. The composite unique needs
    #    NULLS NOT DISTINCT (unset FKs + null branch), added by execute below.
    op.create_table(
        "catalog_sync_map",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("target", sa.String(20), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("mm_kind", sa.String(16), nullable=False),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "modifier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modifiers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "modifier_option_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modifier_options.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("external_parent_id", sa.String(128), nullable=True),
        sa.Column("external_ref", sa.String(200), nullable=True),
        sa.Column("external_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("approved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column(
            "match_method", sa.String(16), nullable=False, server_default="fuzzy"
        ),
        sa.Column("match_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"target IN ({_TARGETS})", name="ck_catalog_sync_map_target"
        ),
        sa.CheckConstraint(
            "mm_kind IN ('category', 'product', 'modifier', 'option')",
            name="ck_catalog_sync_map_kind",
        ),
        sa.CheckConstraint(
            "match_method IN ('exact', 'fuzzy', 'manual')",
            name="ck_catalog_sync_map_method",
        ),
        sa.CheckConstraint(
            "( (category_id IS NOT NULL)::int + (product_id IS NOT NULL)::int "
            "+ (modifier_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int"
            " ) = 1 "
            "AND (category_id IS NULL OR mm_kind = 'category') "
            "AND (product_id IS NULL OR mm_kind = 'product') "
            "AND (modifier_id IS NULL OR mm_kind = 'modifier') "
            "AND (modifier_option_id IS NULL OR mm_kind = 'option')",
            name="ck_catalog_sync_map_one_entity",
        ),
    )
    op.execute(
        "ALTER TABLE catalog_sync_map ADD CONSTRAINT uq_catalog_sync_map_entity "
        "UNIQUE NULLS NOT DISTINCT "
        "(target, branch_id, mm_kind, category_id, product_id, modifier_id, "
        "modifier_option_id)"
    )
    op.create_index(
        "ix_catalog_sync_map_target_branch", "catalog_sync_map", ["target", "branch_id"]
    )
    op.create_index("ix_catalog_sync_map_product", "catalog_sync_map", ["product_id"])

    # 2. aggregator_menu_snapshot — last read of one outlet's menu/hours + diff.
    op.create_table(
        "aggregator_menu_snapshot",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("target", sa.String(20), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("raw", postgresql.JSONB, nullable=True),
        sa.Column("normalized", postgresql.JSONB, nullable=True),
        sa.Column("diff", postgresql.JSONB, nullable=True),
        sa.Column("stats", postgresql.JSONB, nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"target IN ({_TARGETS})", name="ck_aggregator_menu_snapshot_target"
        ),
        sa.CheckConstraint(
            "kind IN ('menu', 'hours')", name="ck_aggregator_menu_snapshot_kind"
        ),
        sa.CheckConstraint(
            "source IN ('http', 'browser', 'foodics_api', 'manual')",
            name="ck_aggregator_menu_snapshot_source",
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'stale', 'error')",
            name="ck_aggregator_menu_snapshot_status",
        ),
    )
    op.execute(
        "ALTER TABLE aggregator_menu_snapshot ADD CONSTRAINT "
        "uq_aggregator_menu_snapshot UNIQUE NULLS NOT DISTINCT "
        "(target, branch_id, kind)"
    )
    op.create_index(
        "ix_aggregator_menu_snapshot_target_branch",
        "aggregator_menu_snapshot",
        ["target", "branch_id"],
    )

    # 3. branch_weekly_hours — canonical per-day marketplace schedule.
    op.create_table(
        "branch_weekly_hours",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.Integer, nullable=False),
        sa.Column("shift_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("opens", sa.String(5), nullable=False),
        sa.Column("closes", sa.String(5), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "branch_id", "weekday", "shift_index", name="uq_branch_weekly_hours_shift"
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6", name="ck_branch_weekly_hours_weekday"
        ),
        sa.CheckConstraint(
            "shift_index >= 0", name="ck_branch_weekly_hours_shift_index"
        ),
        sa.CheckConstraint(
            r"opens ~ '^\d{2}:\d{2}$' AND closes ~ '^\d{2}:\d{2}$'",
            name="ck_branch_weekly_hours_time_format",
        ),
    )
    op.create_index(
        "ix_branch_weekly_hours_branch", "branch_weekly_hours", ["branch_id"]
    )

    # 4. The per-row "goes to the aggregators" switch on products + categories.
    for table in ("products", "categories"):
        op.add_column(
            table,
            sa.Column(
                "sync_to_aggregators",
                sa.Boolean,
                nullable=False,
                server_default="false",
            ),
        )
        op.add_column(
            table,
            sa.Column("sync_channels", postgresql.ARRAY(sa.String), nullable=True),
        )


def downgrade() -> None:
    for table in ("products", "categories"):
        op.drop_column(table, "sync_channels")
        op.drop_column(table, "sync_to_aggregators")
    op.drop_index("ix_branch_weekly_hours_branch", table_name="branch_weekly_hours")
    op.drop_table("branch_weekly_hours")
    op.drop_index(
        "ix_aggregator_menu_snapshot_target_branch",
        table_name="aggregator_menu_snapshot",
    )
    op.drop_table("aggregator_menu_snapshot")
    op.drop_index("ix_catalog_sync_map_product", table_name="catalog_sync_map")
    op.drop_index("ix_catalog_sync_map_target_branch", table_name="catalog_sync_map")
    op.drop_table("catalog_sync_map")
