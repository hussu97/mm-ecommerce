"""Retire the region.

A region was a customer's answer to "which emirate are you in", and it set the
delivery fee. Migration 048 stopped believing it — the pin decides the price
now — and everything since has priced off polygons. What was left was a table
nobody reads, a dropdown nobody needs to answer, and three places where the two
ideas of "where is this going" could quietly disagree.

Gone:

  * `regions` — the nine rows, their fees and their translations.
  * `addresses.region` — the question. Latitude and longitude are already
    NOT NULL on the same row and are strictly better at answering it.
  * `orders.region_id` — the emirate an order was filed under. The zone that
    actually priced it is snapshotted on `order_deliveries.zone_name`, which is
    the same fact measured rather than declared.
  * `branch_regions` — which branch served which emirate. Never read outside
    its own two endpoints, and a branch's catchment is a polygon question now.
  * `delivery_polygons.region_slug` — a label pointing at a table that no
    longer exists. The zone's name is the label.

`delivery_settings` stays exactly as it is, because none of the three numbers
on it were ever regional: the free-delivery threshold is deliberately the same
everywhere, pickup has no zone, and the default is what a pin outside every
shape gets.

Reporting that grouped by region now groups by delivery zone. Old orders keep a
`region` key inside `shipping_address_snapshot` — that is a frozen copy of what
was submitted and is left alone, as snapshots should be.

Irreversible in the sense that matters: the downgrade rebuilds the tables and
columns, empty. The nine region rows and every address's emirate are not
recoverable from anything else, so this migration is the point of no return and
the backup taken before it is the only way back.

Revision ID: 053_retire_regions
Revises: 052_delivery_batching
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "053_retire_regions"
down_revision: Union[str, None] = "052_delivery_batching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("branch_regions")

    # Dropped before the table they point at, or the foreign keys hold it open.
    op.drop_column("orders", "region_id")
    op.drop_column("addresses", "region")
    op.drop_column("delivery_polygons", "region_slug")

    op.drop_table("regions")


def downgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=50), nullable=False, unique=True),
        sa.Column("name_translations", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "delivery_fee", sa.Numeric(10, 2), nullable=False, server_default="0.00"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index("ix_regions_slug", "regions", ["slug"], unique=True)

    op.add_column(
        "delivery_polygons",
        sa.Column("region_slug", sa.String(length=30), nullable=True),
    )
    # Nullable on the way back even though it was NOT NULL going in: the values
    # are gone, and a default emirate stamped on every saved address would be a
    # worse lie than an empty column.
    op.add_column("addresses", sa.Column("region", sa.String(length=30), nullable=True))
    op.add_column(
        "orders",
        sa.Column("region_id", UUID(as_uuid=True), nullable=True),
    )
    # The index and the foreign key that went when the column did, restored
    # under the names `042` gave them — an inline `ForeignKey` here would let
    # Postgres pick `orders_region_id_fkey` instead, and `042`'s downgrade
    # drops `fk_orders_region_id` by name. Both omissions stranded `alembic
    # downgrade base` at this revision, the same trap `088` had for
    # `ix_delivery_batches_polygon_id`.
    op.create_index("ix_orders_region_id", "orders", ["region_id"])
    op.create_foreign_key(
        "fk_orders_region_id",
        "orders",
        "regions",
        ["region_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "branch_regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "region_id",
            UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delivery_fee", sa.Numeric(10, 2), nullable=True),
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
        sa.UniqueConstraint("branch_id", "region_id", name="uq_branch_region"),
    )
