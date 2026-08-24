"""Let orders in the same zone travel together.

One courier run carrying five drops costs about a third per delivery of five
separate runs, and ten drops about a sixth. That difference is larger than any
pricing decision available to us: at AED 47 a single Dubai run loses money
against a AED 25 fee, and at ten drops the same route costs AED 15.70. Batching
is the thing that makes the website beat the aggregators, and it needs no new
capability from Lalamove — it is one `POST /v3/quotations` with fifteen stops
instead of fifteen calls.

Three pieces:

  * `delivery_batch_windows` — slots of the day, per zone, in Dubai time. An
    order that becomes dispatchable inside a window waits for the window to end
    and leaves with everything else that accumulated in it.

  * `delivery_batches` — one courier order carrying many of ours, holding the
    optimised route, the distance and what it cost.

  * `order_deliveries` gains the run it is on, the moment it became
    dispatchable, and which stop in the route is its own.

Seeds the six windows the shop asked for against every Lalamove zone:
00:00–12:00, 12:00–18:00, 18:00–21:00, 21:00–22:00, 22:00–23:00, 23:00–24:00.
They tighten through the evening because that is where the orders are — 58% of
aggregator orders land between 18:00 and 23:00, so an hour of waiting late in
the day fills a van that a morning hour would not.

Third-party zones get no windows. There is no run of ours for them to share.

Revision ID: 052_delivery_batching
Revises: 051_order_deliveries
Create Date: 2026-08-02
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "052_delivery_batching"
down_revision: Union[str, None] = "051_order_deliveries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# label, start (h, m), end (h, m). End hour 24 is midnight closing the day.
SEED_WINDOWS: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Batch 1", (0, 0), (12, 0)),
    ("Batch 2", (12, 0), (18, 0)),
    ("Batch 3", (18, 0), (21, 0)),
    ("Batch 4", (21, 0), (22, 0)),
    ("Batch 5", (22, 0), (23, 0)),
    ("Batch 6", (23, 0), (24, 0)),
]


def upgrade() -> None:
    op.create_table(
        "delivery_batch_windows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "polygon_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
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
        "ix_delivery_batch_windows_polygon_id",
        "delivery_batch_windows",
        ["polygon_id"],
    )

    op.create_table(
        "delivery_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "polygon_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
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
    )
    op.create_index(
        "ix_delivery_batches_polygon_id", "delivery_batches", ["polygon_id"]
    )
    op.create_index("ix_delivery_batches_window_id", "delivery_batches", ["window_id"])
    op.create_index("ix_delivery_batches_status", "delivery_batches", ["status"])
    # The sweeper's only query: everything pending and due. Composite so it is
    # an index scan rather than a filter over every batch we have ever run.
    op.create_index(
        "ix_delivery_batches_due", "delivery_batches", ["status", "dispatch_at"]
    )
    op.create_index(
        "ix_delivery_batches_courier_order_id",
        "delivery_batches",
        ["courier_order_id"],
    )

    op.add_column(
        "order_deliveries",
        sa.Column(
            "polygon_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygons.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        "order_deliveries",
        sa.Column("dispatchable_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_deliveries", sa.Column("stop_sequence", sa.Integer(), nullable=True)
    )
    op.add_column(
        "order_deliveries", sa.Column("stop_id", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_order_deliveries_polygon_id", "order_deliveries", ["polygon_id"]
    )
    op.create_index("ix_order_deliveries_batch_id", "order_deliveries", ["batch_id"])

    # ── Seed ──────────────────────────────────────────────────────────────────
    # Every Lalamove polygon on every map, not just the live one: a map that is
    # published later should arrive with a schedule rather than silently
    # dispatching one order at a time.
    conn = op.get_bind()
    polygons = conn.execute(
        sa.text(
            "SELECT id FROM delivery_polygons WHERE fulfilment_provider = 'lalamove'"
        )
    ).fetchall()

    for (polygon_id,) in polygons:
        for label, (start_h, start_m), (end_h, end_m) in SEED_WINDOWS:
            conn.execute(
                sa.text(
                    "INSERT INTO delivery_batch_windows "
                    "(id, polygon_id, label, start_hour, start_minute, end_hour, end_minute) "
                    "VALUES (:id, :polygon_id, :label, :sh, :sm, :eh, :em)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "polygon_id": str(polygon_id),
                    "label": label,
                    "sh": start_h,
                    "sm": start_m,
                    "eh": end_h,
                    "em": end_m,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_order_deliveries_batch_id", table_name="order_deliveries")
    op.drop_index("ix_order_deliveries_polygon_id", table_name="order_deliveries")
    for name in (
        "stop_id",
        "stop_sequence",
        "dispatchable_at",
        "batch_id",
        "polygon_id",
    ):
        op.drop_column("order_deliveries", name)

    op.drop_index("ix_delivery_batches_courier_order_id", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_due", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_status", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_window_id", table_name="delivery_batches")
    op.drop_index("ix_delivery_batches_polygon_id", table_name="delivery_batches")
    op.drop_table("delivery_batches")

    op.drop_index(
        "ix_delivery_batch_windows_polygon_id", table_name="delivery_batch_windows"
    )
    op.drop_table("delivery_batch_windows")
