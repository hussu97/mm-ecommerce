"""Catalog & hours sync scaffold: reuse the item map, add snapshots + weekly hours.

The read/model half of the central catalog-&-hours sync (writes stay behind
`CATALOG_SYNC_ENABLED`, off). All additive, and deliberately **reuse** the existing
identity plumbing rather than add a parallel map that could drift:

1. `external_item_map` gains `category_id` + the `category` kind — so the one
   catalogue map now covers categories as well as products/options. Order
   reconciliation reads only product/option rows and is untouched.
2. `aggregator_menu_snapshot` — the last read of one outlet's live menu/hours
   (raw + normalized + computed diff), read by the drift report; the writer reads
   each item's live id straight off it, so no per-outlet id map is stored.
3. `branch_weekly_hours` — a canonical per-day, multi-shift schedule MM has not
   had; the source of truth the hours writer fans out per portal.
4. `products` / `categories` gain `sync_to_aggregators` + `sync_channels` — the
   per-row "goes to the aggregators" switch, deliberately separate from
   `sales_channels` (the audit found the two disagree for 16 signature products).

Branch↔outlet identity is reused from `aggregator_branch_map` / `foodics_branch_map`
(no change here). See `docs/aggregator-catalog-hours-sync-audit.md`.

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

_ITEM_MAP_ONE_ENTITY_OLD = (
    "NOT (product_id IS NOT NULL AND modifier_option_id IS NOT NULL) "
    "AND (product_id IS NULL OR mm_kind = 'product') "
    "AND (modifier_option_id IS NULL OR mm_kind = 'option')"
)
_ITEM_MAP_ONE_ENTITY_NEW = (
    "( (product_id IS NOT NULL)::int + (modifier_option_id IS NOT NULL)::int "
    "+ (category_id IS NOT NULL)::int ) <= 1 "
    "AND (product_id IS NULL OR mm_kind = 'product') "
    "AND (modifier_option_id IS NULL OR mm_kind = 'option') "
    "AND (category_id IS NULL OR mm_kind = 'category')"
)


def upgrade() -> None:
    # 1. Extend the existing item map with categories — reuse, don't duplicate.
    op.add_column(
        "external_item_map",
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_external_item_map_kind", "external_item_map", type_="check")
    op.create_check_constraint(
        "ck_external_item_map_kind",
        "external_item_map",
        "mm_kind IN ('product', 'option', 'category')",
    )
    op.drop_constraint(
        "ck_external_item_map_one_entity", "external_item_map", type_="check"
    )
    op.create_check_constraint(
        "ck_external_item_map_one_entity",
        "external_item_map",
        _ITEM_MAP_ONE_ENTITY_NEW,
    )

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

    # Undo the item-map category extension. Any category rows must go first or the
    # narrowed CHECK/column drop would fail.
    op.execute("DELETE FROM external_item_map WHERE mm_kind = 'category'")
    op.drop_constraint(
        "ck_external_item_map_one_entity", "external_item_map", type_="check"
    )
    op.create_check_constraint(
        "ck_external_item_map_one_entity",
        "external_item_map",
        _ITEM_MAP_ONE_ENTITY_OLD,
    )
    op.drop_constraint("ck_external_item_map_kind", "external_item_map", type_="check")
    op.create_check_constraint(
        "ck_external_item_map_kind",
        "external_item_map",
        "mm_kind IN ('product', 'option')",
    )
    op.drop_column("external_item_map", "category_id")
