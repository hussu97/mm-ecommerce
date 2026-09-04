"""Remove delivery batching entirely; every courier dispatches directly.

Batching is gone: no shared runs, no windows, no groups. Every polygon
dispatches on its own the moment an order is ready, Lalamove included.

The FK columns go first (`delivery_polygons.batch_group_id`,
`order_deliveries.batch_id` / `stop_sequence` / `stop_id`) and
`couriers.supports_batching`, then the three tables (`delivery_batches`,
`delivery_batch_windows`, `delivery_batch_groups`).
`order_deliveries.polygon_id` and `dispatchable_at` are NOT batching and stay —
one is the priced zone, the other is when the box became ready, which the
single-order retry sweep still reads.

This is structural only. It changes no fee and no courier, so no cart needs
re-resolving — the courier re-simulation ships as its own map version.

Revision ID: 176_remove_batching
Revises: 175_per_area_courier_map
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "176_remove_batching"
down_revision: Union[str, None] = "175_per_area_courier_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batching FK columns. Drop before the tables they point at.
    op.drop_index("ix_order_deliveries_batch_id", table_name="order_deliveries")
    op.drop_column("order_deliveries", "batch_id")
    op.drop_column("order_deliveries", "stop_sequence")
    op.drop_column("order_deliveries", "stop_id")

    op.drop_index("ix_delivery_polygons_batch_group_id", table_name="delivery_polygons")
    op.drop_column("delivery_polygons", "batch_group_id")

    op.drop_column("couriers", "supports_batching")

    # The three tables. `delivery_batches` references windows and groups, so it
    # goes first; groups is referenced by both the others and by `couriers.code`,
    # so it goes last.
    op.drop_table("delivery_batches")
    op.drop_table("delivery_batch_windows")
    op.drop_table("delivery_batch_groups")


def downgrade() -> None:
    # Recreates the batching schema in its final shape (empty — the runs, groups
    # and windows are unrecoverable). The provider flip is deliberately not
    # reversed: it is a commercial number the new map already carries, not part
    # of the batching structure.
    conn = op.get_bind()

    op.add_column(
        "couriers",
        sa.Column(
            "supports_batching", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    conn.execute(
        sa.text("UPDATE couriers SET supports_batching = true WHERE code = 'lalamove'")
    )

    # ── delivery_batch_groups ──────────────────────────────────────────────
    op.create_table(
        "delivery_batch_groups",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "courier_code",
            sa.String(20),
            sa.ForeignKey("couriers.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "delivery_minutes_after_dispatch",
            sa.Integer(),
            nullable=False,
            server_default="90",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    )
    op.create_index(
        "ix_delivery_batch_groups_courier_code",
        "delivery_batch_groups",
        ["courier_code"],
    )

    # ── delivery_batch_windows (final shape: group_id, no polygon_id) ───────
    op.create_table(
        "delivery_batch_windows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("start_hour", sa.Integer(), nullable=False),
        sa.Column("start_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_hour", sa.Integer(), nullable=False),
        sa.Column("end_minute", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "start_hour BETWEEN 0 AND 23 AND end_hour BETWEEN 0 AND 24",
            name="ck_batch_window_hours",
        ),
        sa.CheckConstraint(
            "start_minute BETWEEN 0 AND 59 AND end_minute BETWEEN 0 AND 59",
            name="ck_batch_window_minutes",
        ),
    )
    op.create_index(
        "ix_delivery_batch_windows_group_id", "delivery_batch_windows", ["group_id"]
    )

    # ── delivery_batches (final shape: group_id + retry cols, no polygon_id) ─
    op.create_table(
        "delivery_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "window_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_windows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("window_label", sa.String(length=60), nullable=True),
        sa.Column("dispatch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("courier_order_id", sa.String(length=64), nullable=True),
        sa.Column("quotation_id", sa.String(length=64), nullable=True),
        sa.Column("share_link", sa.Text(), nullable=True),
        sa.Column("courier_status", sa.String(length=30), nullable=True),
        sa.Column("driver_id", sa.String(length=64), nullable=True),
        sa.Column("driver_name", sa.String(length=150), nullable=True),
        sa.Column("driver_phone", sa.String(length=30), nullable=True),
        sa.Column("driver_plate", sa.String(length=30), nullable=True),
        sa.Column("stop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("cost_total", sa.Numeric(10, 2), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("price_breakdown", JSONB, nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_payload", JSONB, nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatching', 'dispatched', 'failed', 'cancelled')",
            name="ck_delivery_batches_status_allowed",
        ),
    )
    op.create_index("ix_delivery_batches_window_id", "delivery_batches", ["window_id"])
    op.create_index("ix_delivery_batches_status", "delivery_batches", ["status"])
    op.create_index(
        "ix_delivery_batches_due", "delivery_batches", ["status", "dispatch_at"]
    )
    op.create_index(
        "ix_delivery_batches_courier_order_id",
        "delivery_batches",
        ["courier_order_id"],
    )
    op.create_index("ix_delivery_batches_group_id", "delivery_batches", ["group_id"])
    op.create_index(
        "ix_delivery_batches_next_attempt_at",
        "delivery_batches",
        ["next_attempt_at"],
    )

    # ── the FK columns back onto the live tables ───────────────────────────
    op.add_column(
        "delivery_polygons",
        sa.Column(
            "batch_group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_delivery_polygons_batch_group_id",
        "delivery_polygons",
        ["batch_group_id"],
    )

    op.add_column(
        "order_deliveries",
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "order_deliveries", sa.Column("stop_sequence", sa.Integer(), nullable=True)
    )
    op.add_column(
        "order_deliveries", sa.Column("stop_id", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_order_deliveries_batch_id", "order_deliveries", ["batch_id"])
