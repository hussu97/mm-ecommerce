"""Delivery zones on orders, online-order routing, and parked checks.

Revision ID: 042_zones_devices_parking
Revises: 041_courses_and_nutrition
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "042_zones_devices_parking"
down_revision = "041_courses_and_nutrition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which delivery zone an order went to. Regions already carry the fee;
    # this records which one was actually used, so sales can be reported by
    # zone instead of guessed from a free-text address snapshot.
    op.add_column(
        "orders", sa.Column("region_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_orders_region_id",
        "orders",
        "regions",
        ["region_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_orders_region_id", "orders", ["region_id"])

    # Which branch serves which zone. A zone with no branch is unserviced,
    # and one served by two branches is a genuine overlap the dispatcher
    # resolves — so this is a plain many-to-many, not a column on either side.
    op.create_table(
        "branch_regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "delivery_fee",
            sa.Numeric(10, 2),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.UniqueConstraint("branch_id", "region_id", name="uq_branch_region"),
    )

    # Which terminal online orders land on. Without it a branch with three
    # iPads has no answer to "where does a web order print?".
    op.add_column(
        "devices",
        sa.Column(
            "receives_online_orders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "receives_online_orders")
    op.drop_table("branch_regions")
    op.drop_index("ix_orders_region_id", table_name="orders")
    op.drop_constraint("fk_orders_region_id", "orders", type_="foreignkey")
    op.drop_column("orders", "region_id")
